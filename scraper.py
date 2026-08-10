import os
import sys
import json
import re
import argparse
import httpx
from bs4 import BeautifulSoup
from openai import OpenAI
from dotenv import load_dotenv

# Load env variables from .env file if present
load_dotenv()

def clean_slug(text):
    return re.sub(r'[^a-z0-9\-]', '', text.lower().replace(' ', '-').replace('_', '-'))

def fetch_webpage(url):
    print(f"Fetching: {url}")
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9",
        "Accept-Encoding": "gzip, deflate, br",
        "Connection": "keep-alive",
        "Upgrade-Insecure-Requests": "1",
        "Sec-Fetch-Dest": "document",
        "Sec-Fetch-Mode": "navigate",
        "Sec-Fetch-Site": "none",
        "Sec-Fetch-User": "?1"
    }
    try:
        # Enable HTTP/2 or fallback if not installed
        client = httpx.Client(http2=True)
        response = client.get(url, headers=headers, follow_redirects=True, timeout=15.0)
        response.raise_for_status()
        return response.text
    except Exception as e:
        print(f"HTTP/2 fetch failed or not supported, retrying with standard HTTP/1.1: {e}")
        try:
            response = httpx.get(url, headers=headers, follow_redirects=True, timeout=15.0)
            response.raise_for_status()
            return response.text
        except Exception as e2:
            print(f"Error fetching webpage: {e2}")
            return None

def parse_course_info(html_content, url):
    soup = BeautifulSoup(html_content, 'html.parser')
    
    # Attempt to extract title
    title_el = soup.find('h1')
    title = title_el.text.strip() if title_el else "Unknown Course"
    
    # Attempt to extract description
    desc_el = soup.find('meta', attrs={'name': 'description'})
    description = desc_el['content'].strip() if desc_el else ""
    if not description:
        # Fallback to finding paragraphs
        p_el = soup.find('p')
        description = p_el.text.strip() if p_el else "No description found."
    
    # Extract syllabus list items or header titles
    syllabus = []
    # Look for lists or headings that might represent modules/lessons
    # For DeepLearning.AI, let's try common selectors or header classes
    lesson_elements = soup.select('h2, h3, .syllabus-item, .course-outline-item, li')
    
    seen = set()
    for el in lesson_elements:
        text = el.text.strip()
        # Clean up text and exclude short or duplicate lines
        if len(text) > 10 and len(text) < 120 and text not in seen:
            # Simple heuristic: exclude site navigation/footer terms
            if any(term in text.lower() for term in ["login", "sign up", "terms of use", "privacy policy", "all rights reserved"]):
                continue
            syllabus.append(text)
            seen.add(text)
            
    # Limit default syllabus elements if it's too long/noisy
    if len(syllabus) > 25:
        syllabus = syllabus[:25]
        
    if not syllabus:
        syllabus = ["Introduction", "Core Concepts", "Implementation Details", "Summary & Review"]
        
    return {
        "title": title,
        "description": description,
        "url": url,
        "slug": clean_slug(title),
        "syllabus": syllabus
    }

def summarize_with_openai(title, lesson_name, transcript_content):
    # Support configuration for OpenRouter / standard OpenAI from environment/.env
    api_key = os.environ.get("OPENROUTER_API_KEY")
    base_url = os.environ.get("OPENROUTER_BASE_URL", "https://openrouter.ai/api/v1")
    model = os.environ.get("OPENROUTER_MODEL", "nvidia/nemotron-3-embed-1b:free")
    
    if api_key:
        client = OpenAI(
            base_url=base_url,
            api_key=api_key,
            default_headers={
                "HTTP-Referer": "https://github.com/deeplearning-ai-helper",
                "X-Title": "DeepLearning.AI Recall Helper"
            }
        )
    else:
        # Fallback to standard OpenAI key
        client = OpenAI()
        model = "gpt-4o-mini"
        
    prompt = f"""
    You are an expert AI teaching assistant. Summarize the following lesson video transcript for the course "{title}".
    
    Lesson/Video Name: {lesson_name}
    
    Transcript:
    \"\"\"
    {transcript_content}
    \"\"\"
    
    Provide the response strictly in JSON format matching the schema below:
    {{
      "summary": "A concise 2-3 paragraph summary detailing the core concepts explained in the video.",
      "key_takeaways": [
         "Key takeaway 1 with explanation",
         "Key takeaway 2 with explanation",
         "Key takeaway 3 with explanation"
      ],
      "revise_recall": [
         {{
           "question": "A conceptual flashcard question asking about a key term or mechanism from this lesson?",
           "answer": "A clear, concise answer to the question."
         }},
         {{
           "question": "Another high-value question testing understanding of when/why to use this concept?",
           "answer": "A clear, concise answer."
         }}
      ]
    }}
    """
    
    try:
        response = client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": "You are a helpful assistant that outputs JSON format."},
                {"role": "user", "content": prompt}
            ],
            response_format={"type": "json_object"}
        )
        return json.loads(response.choices[0].message.content)
    except Exception as e:
        print(f"Error calling LLM for {lesson_name}: {e}")
        # Return fallback mock structure
        return {
            "summary": f"This lesson covers '{lesson_name}'. Add transcripts to generate high-quality OpenAI summaries.",
            "key_takeaways": [
                f"Introduction to the core themes in {lesson_name}.",
                "Understand the relationship between this and the general course syllabus."
            ],
            "revise_recall": [
                {
                    "question": f"What is the primary topic of {lesson_name}?",
                    "answer": f"The primary focus is understanding the foundations of {lesson_name}."
                }
            ]
        }

def process_course(url, is_specialization):
    html_content = fetch_webpage(url)
    if not html_content:
        print("Failed to fetch webpage content.")
        sys.exit(1)
        
    course_info = parse_course_info(html_content, url)
    slug = course_info["slug"]
    
    print(f"\nProcessing course: {course_info['title']}")
    print(f"Syllabus items found: {len(course_info['syllabus'])}")
    
    # Look for local transcripts in transcripts/{slug}/
    transcripts_dir = os.path.join("transcripts", slug)
    transcripts = {}
    
    if os.path.exists(transcripts_dir):
        print(f"Found transcripts directory: {transcripts_dir}")
        for file in sorted(os.listdir(transcripts_dir)):
            if file.endswith((".txt", ".vtt", ".srt")):
                file_path = os.path.join(transcripts_dir, file)
                # Clean up filename to match syllabus item if possible, or use filename
                name_without_ext = os.path.splitext(file)[0].replace("-", " ").title()
                with open(file_path, "r", encoding="utf-8") as f:
                    transcripts[name_without_ext] = f.read()
                    
    # Generate summaries
    lessons_data = []
    for idx, item in enumerate(course_info["syllabus"]):
        print(f"Processing lesson {idx+1}/{len(course_info['syllabus'])}: {item}")
        
        # Match with a transcript if we have one, otherwise supply placeholder context
        matched_transcript = ""
        for trans_name, content in transcripts.items():
            # simple substring match
            if clean_slug(trans_name) in clean_slug(item) or clean_slug(item) in clean_slug(trans_name):
                matched_transcript = content
                break
                
        if not matched_transcript and transcripts:
            # Fallback: take transcript by index order if filenames don't match
            trans_keys = list(transcripts.keys())
            if idx < len(trans_keys):
                matched_transcript = transcripts[trans_keys[idx]]
                
        if not matched_transcript:
            matched_transcript = f"Placeholder transcript content for {item}. Provide transcripts in transcripts/{slug}/ to summarize."
            
        summary_result = summarize_with_openai(course_info["title"], item, matched_transcript)
        
        lessons_data.append({
            "id": idx + 1,
            "title": item,
            "summary": summary_result["summary"],
            "key_takeaways": summary_result["key_takeaways"],
            "revise_recall": summary_result["revise_recall"]
        })
        
    course_data = {
        "title": course_info["title"],
        "slug": slug,
        "description": course_info["description"],
        "url": url,
        "is_specialization": is_specialization,
        "lessons": lessons_data
    }
    
    # Save to courses.json
    db_path = "courses.json"
    existing_courses = []
    if os.path.exists(db_path):
        try:
            with open(db_path, "r", encoding="utf-8") as f:
                existing_courses = json.load(f)
        except Exception:
            existing_courses = []
            
    # Update existing course or append
    updated = False
    for i, c in enumerate(existing_courses):
        if c["slug"] == slug:
            existing_courses[i] = course_data
            updated = True
            break
    if not updated:
        existing_courses.append(course_data)
        
    with open(db_path, "w", encoding="utf-8") as f:
        json.dump(existing_courses, f, indent=2)
        
    print(f"\nSaved course data successfully to {db_path}")

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="DeepLearning.AI Scraper & Summarizer")
    parser.add_argument("--url", required=True, help="URL of the course or specialization")
    parser.add_argument("--specialization", action="store_true", help="Set if target is a specialization")
    args = parser.parse_args()
    
    # Create transcripts directory skeleton if it doesn't exist
    os.makedirs("transcripts", exist_ok=True)
    
    process_course(args.url, args.specialization)
