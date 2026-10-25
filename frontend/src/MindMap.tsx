import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import {
  ReactFlow,
  Background,
  Controls,
  Handle,
  Position,
  MarkerType,
  type Node,
  type Edge,
  type NodeProps,
} from '@xyflow/react';
import '@xyflow/react/dist/style.css';
import dagre from 'dagre';
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import { Sparkles, MessageCirclePlus, X, Loader2, Globe } from 'lucide-react';
import styles from './MindMap.module.css';

const API_BASE_URL = 'http://localhost:8000';

type MMStatus = 'none' | 'seeding' | 'ready' | 'error' | 'loading';

interface MMNode {
  id: number;
  parent_id: number | null;
  node_type: 'root' | 'concept' | 'leaf' | 'question';
  title: string;
  summary: string | null;
  source: string;
  sources: { type: string; url?: string }[];
  expanded: boolean;
  position: number;
}

const NODE_W = 240;
const NODE_H = 104;

// ---- dagre layout: left-to-right growth (seed -> branches -> leaves) ----
function layout(nodes: MMNode[]): Record<number, { x: number; y: number }> {
  const g = new dagre.graphlib.Graph();
  g.setGraph({ rankdir: 'LR', nodesep: 24, ranksep: 90 });
  g.setDefaultEdgeLabel(() => ({}));
  nodes.forEach(n => g.setNode(String(n.id), { width: NODE_W, height: NODE_H }));
  nodes.forEach(n => { if (n.parent_id != null) g.setEdge(String(n.parent_id), String(n.id)); });
  dagre.layout(g);
  const pos: Record<number, { x: number; y: number }> = {};
  nodes.forEach(n => {
    const p = g.node(String(n.id));
    pos[n.id] = { x: p.x - NODE_W / 2, y: p.y - NODE_H / 2 };
  });
  return pos;
}

// ---- custom node card ----
interface CardData {
  node: MMNode;
  busy: boolean;
  selected: boolean;
  onSelect: (id: number) => void;
  onAsk: (id: number) => void;
  [key: string]: unknown;
}

const TYPE_LABEL: Record<MMNode['node_type'], string> = {
  root: 'Seed', concept: 'Concept', leaf: 'Detail', question: 'Q&A',
};

function ConceptCard({ data }: NodeProps<Node<CardData>>) {
  const { node, busy, selected, onSelect, onAsk } = data;
  const typeClass =
    node.node_type === 'root' ? styles.root :
    node.node_type === 'question' ? styles.question :
    node.node_type === 'leaf' ? styles.leaf : styles.concept;
  return (
    <div
      className={`${styles.card} ${typeClass} ${selected ? styles.cardSelected : ''}`}
      onClick={() => onSelect(node.id)}
    >
      <Handle type="target" position={Position.Left} className={styles.handle} />
      <div className={styles.cardType}>
        {TYPE_LABEL[node.node_type]}
        {node.source === 'web' && <Globe size={11} />}
      </div>
      <div className={styles.cardTitle}>{node.title}</div>
      <div className={styles.cardActions}>
        {busy ? (
          <span className={styles.cardBusy}><Loader2 size={13} className={styles.spin} /> thinking…</span>
        ) : (
          <button className={styles.cardBtn} onClick={(e) => { e.stopPropagation(); onAsk(node.id); }}>
            <MessageCirclePlus size={13} /> Ask
          </button>
        )}
      </div>
      <Handle type="source" position={Position.Right} className={styles.handle} />
    </div>
  );
}

export default function MindMap({ catalogId }: { catalogId: number }) {
  const [status, setStatus] = useState<MMStatus>('loading');
  const [error, setError] = useState<string | null>(null);
  const [nodes, setNodes] = useState<MMNode[]>([]);
  const [progress, setProgress] = useState<{ built: number; total: number }>({ built: 0, total: 0 });
  const [selectedId, setSelectedId] = useState<number | null>(null);
  const [busyId, setBusyId] = useState<number | null>(null);
  const [asking, setAsking] = useState<number | null>(null);
  const [askText, setAskText] = useState('');
  const seedingRef = useRef(false);

  const fetchMap = useCallback(() => {
    fetch(`${API_BASE_URL}/api/mindmap/${catalogId}`, { credentials: 'include' })
      .then(r => r.json())
      .then(data => {
        setStatus(data.status);
        setError(data.error);
        setNodes(data.nodes || []);
        setProgress({ built: data.built_concepts || 0, total: data.total_concepts || 0 });
      })
      .catch(() => setError('Failed to load mind map'));
  }, [catalogId]);

  useEffect(() => { fetchMap(); }, [fetchMap]);

  // Poll while the background seed job runs.
  useEffect(() => {
    if (status !== 'seeding') { seedingRef.current = false; return; }
    seedingRef.current = true;
    const t = setInterval(fetchMap, 2500);
    return () => clearInterval(t);
  }, [status, fetchMap]);

  const seed = () => {
    setStatus('seeding');
    fetch(`${API_BASE_URL}/api/mindmap/${catalogId}/seed`, { method: 'POST', credentials: 'include' })
      .then(async r => { if (!r.ok) throw new Error((await r.json()).detail); return r.json(); })
      .then(fetchMap)
      .catch(e => { setError(e.message); setStatus('error'); });
  };

  const submitAsk = (id: number) => {
    if (!askText.trim()) return;
    setBusyId(id);
    const q = askText.trim();
    setAskText('');
    setAsking(null);
    fetch(`${API_BASE_URL}/api/mindmap/node/${id}/ask`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      credentials: 'include',
      body: JSON.stringify({ question: q }),
    })
      .then(async r => { if (!r.ok) throw new Error((await r.json()).detail); return r.json(); })
      .then(data => { setNodes(prev => [...prev, data.node]); setSelectedId(data.node.id); })
      .catch(e => setError(e.message))
      .finally(() => setBusyId(null));
  };

  const nodeTypes = useMemo(() => ({ concept: ConceptCard }), []);

  const rfNodes: Node<CardData>[] = useMemo(() => {
    const pos = layout(nodes);
    return nodes.map(n => ({
      id: String(n.id),
      type: 'concept',
      position: pos[n.id] || { x: 0, y: 0 },
      data: {
        node: n,
        busy: busyId === n.id,
        selected: selectedId === n.id,
        onSelect: setSelectedId,
        onAsk: (id: number) => { setAsking(id); setSelectedId(id); },
      },
    }));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [nodes, busyId, selectedId]);

  const rfEdges: Edge[] = useMemo(
    () => nodes.filter(n => n.parent_id != null).map(n => ({
      id: `e${n.parent_id}-${n.id}`,
      source: String(n.parent_id),
      target: String(n.id),
      animated: n.node_type === 'question',
      style: { stroke: n.node_type === 'question' ? '#8b5cf6' : 'rgba(255,107,0,0.5)' },
      markerEnd: { type: MarkerType.ArrowClosed, color: n.node_type === 'question' ? '#8b5cf6' : '#ff6b00' },
    })),
    [nodes],
  );

  const selected = nodes.find(n => n.id === selectedId) || null;

  if (status === 'loading') {
    return <div className={styles.center}><Loader2 className={styles.spin} /> Loading mind map…</div>;
  }

  if (status === 'none' || (status === 'error' && nodes.length === 0)) {
    return (
      <div className={styles.center}>
        <Sparkles size={40} className={styles.seedIcon} />
        <h3>Grow a mind map</h3>
        <p>Turn this course's transcripts into an explorable map. Start from a seed concept, expand branches, and ask questions to learn incrementally.</p>
        {error && <p className={styles.err}>{error}</p>}
        <button className={styles.seedBtn} onClick={seed}><Sparkles size={16} /> Grow mind map</button>
      </div>
    );
  }

  if (status === 'seeding') {
    const pct = progress.total > 0 ? Math.round((progress.built / progress.total) * 100) : 0;
    return (
      <div className={styles.center}>
        <Loader2 size={34} className={styles.spin} />
        <h3>Building your mind map…</h3>
        <p>
          {progress.total > 0
            ? `Building section ${Math.min(progress.built + 1, progress.total)} of ${progress.total}…`
            : 'Summarizing lessons and planting the seed…'}
        </p>
        <div className={styles.progressTrack}><div className={styles.progressFill} style={{ width: `${pct}%` }} /></div>
        <p className={styles.progressHint}>Built once, then shared — this runs in the background.</p>
      </div>
    );
  }

  return (
    <div className={styles.wrap}>
      {error && <div className={styles.errBar} onClick={() => setError(null)}>{error} ✕</div>}
      <div className={styles.canvas}>
        <ReactFlow
          nodes={rfNodes}
          edges={rfEdges}
          nodeTypes={nodeTypes}
          fitView
          proOptions={{ hideAttribution: true }}
          minZoom={0.2}
        >
          <Background color="rgba(255,255,255,0.06)" gap={24} />
          <Controls showInteractive={false} />
        </ReactFlow>
      </div>

      {selected && (
        <aside className={styles.panel}>
          <button className={styles.panelClose} onClick={() => setSelectedId(null)}><X size={18} /></button>
          <div className={styles.panelType}>{selected.node_type === 'question' ? 'Q&A branch' : selected.node_type === 'root' ? 'Seed concept' : selected.node_type === 'leaf' ? 'Detail' : 'Concept'}</div>
          <h2 className={styles.panelTitle}>{selected.title}</h2>
          <div className={styles.panelBody}>
            <ReactMarkdown remarkPlugins={[remarkGfm]}>{selected.summary || '_No detail yet._'}</ReactMarkdown>
          </div>

          {selected.sources?.length > 0 && (
            <div className={styles.sources}>
              {selected.sources.filter(s => s.url).map((s, i) => (
                <a key={i} href={s.url} target="_blank" rel="noopener noreferrer" className={styles.sourceChip}>
                  <Globe size={11} /> {new URL(s.url!).hostname}
                </a>
              ))}
            </div>
          )}

          <div className={styles.panelActions}>
            {asking === selected.id ? (
              <form className={styles.askForm} onSubmit={e => { e.preventDefault(); submitAsk(selected.id); }}>
                <input
                  autoFocus
                  className={styles.askInput}
                  placeholder="Ask about this concept…"
                  value={askText}
                  onChange={e => setAskText(e.target.value)}
                />
                <button className={styles.panelBtn} type="submit" disabled={busyId === selected.id}>
                  {busyId === selected.id ? <Loader2 size={14} className={styles.spin} /> : 'Ask'}
                </button>
              </form>
            ) : (
              busyId !== selected.id && (
                <button className={styles.panelBtn} onClick={() => setAsking(selected.id)}>
                  <MessageCirclePlus size={14} /> Ask a question
                </button>
              )
            )}
            {busyId === selected.id && <span className={styles.panelBusy}><Loader2 size={14} className={styles.spin} /> thinking…</span>}
          </div>
        </aside>
      )}
    </div>
  );
}
