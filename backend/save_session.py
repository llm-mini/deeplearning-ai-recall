"""
One-time setup: capture a logged-in deeplearning.ai browser session.

Launches a real (visible) Chromium window. Log in manually to
learn.deeplearning.ai (and Coursera, if you need specializations) in that
window. When done, return to the terminal and press Enter -- the cookies +
tokens are saved to storage_state.json and reused by the backend scraper
(no re-login, no OAuth automation).

Run from the backend/ directory:
    python save_session.py
"""

from playwright.sync_api import sync_playwright

STATE_FILE = "storage_state.json"
# Auth must be established on learn.deeplearning.ai itself
# (its token is per-subdomain, not shared from www).
START_URL = "https://learn.deeplearning.ai/"


def main():
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=False)
        context = browser.new_context()
        page = context.new_page()
        page.goto(START_URL)

        print("\n" + "=" * 70)
        print("A Chromium window opened on learn.deeplearning.ai.")
        print("1. Log in (Log In button -> your credentials).")
        print("2. IMPORTANT: open the actual COURSE you want and click into a")
        print("   VIDEO lesson. Confirm it is UNLOCKED -- you should see your")
        print("   avatar (top-right) and the 'Show Transcript' button, and NO")
        print("   'Log in to continue' popup. This proves learn.deeplearning.ai")
        print("   auth is active so it gets saved.")
        print("3. For Coursera specializations, also open coursera.org + log in.")
        print("4. Then return HERE and press Enter to save the session.")
        print("=" * 70)
        input("\nPress Enter once a video lesson is unlocked for you... ")

        context.storage_state(path=STATE_FILE)
        print(f"\nSaved session -> {STATE_FILE}")

        # Sanity check: did we capture a learn.deeplearning.ai auth context?
        import json
        st = json.load(open(STATE_FILE, encoding="utf-8"))
        learn_cookies = [c for c in st.get("cookies", [])
                         if "learn.deeplearning.ai" in c["domain"]]
        learn_ls = [o for o in st.get("origins", [])
                    if "learn.deeplearning.ai" in o["origin"]]
        print(f"  learn.deeplearning.ai cookies: {len(learn_cookies)}, "
              f"localStorage origins: {len(learn_ls)}")
        if not learn_cookies and not learn_ls:
            print("  WARNING: no learn.deeplearning.ai auth captured -- make sure")
            print("  you actually opened an unlocked lesson before pressing Enter.")
        browser.close()


if __name__ == "__main__":
    main()
