"use client";

import { useRouter } from "next/navigation";
import { useEffect, useRef, useState } from "react";

import { searchStocks } from "../lib/api";
import { StockSearchResult } from "../types";

/* Search by symbol or company name; Enter opens the full analysis page. */
export function StockSearch() {
  const router = useRouter();
  const [query, setQuery] = useState("");
  const [results, setResults] = useState<StockSearchResult[]>([]);
  const [open, setOpen] = useState(false);
  const [active, setActive] = useState(0);
  const boxRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const q = query.trim();
    if (q.length < 2) { setResults([]); return; }
    let alive = true;
    const id = setTimeout(() => {
      searchStocks(q).then((r) => { if (alive) { setResults(r); setActive(0); setOpen(true); } }).catch(() => alive && setResults([]));
    }, 180);
    return () => { alive = false; clearTimeout(id); };
  }, [query]);

  useEffect(() => {
    const close = (e: MouseEvent) => { if (!boxRef.current?.contains(e.target as Node)) setOpen(false); };
    document.addEventListener("mousedown", close);
    return () => document.removeEventListener("mousedown", close);
  }, []);

  function go(symbol: string) {
    const clean = symbol.trim().toUpperCase().replace(/\.NS$/, "");
    if (!clean) return;
    setOpen(false);
    setQuery("");
    router.push(`/analyze/${encodeURIComponent(clean)}`);
  }

  function onKey(e: React.KeyboardEvent<HTMLInputElement>) {
    if (e.key === "ArrowDown") { e.preventDefault(); setActive((a) => Math.min(a + 1, results.length - 1)); setOpen(true); }
    else if (e.key === "ArrowUp") { e.preventDefault(); setActive((a) => Math.max(a - 1, 0)); }
    else if (e.key === "Enter") { e.preventDefault(); go(results[active]?.symbol ?? query); }
    else if (e.key === "Escape") setOpen(false);
  }

  return (
    <div className="ss" ref={boxRef}>
      <input
        className="ss-input"
        type="search"
        placeholder="Analyse a stock — name or symbol"
        aria-label="Search for a stock to analyse"
        value={query}
        onChange={(e) => setQuery(e.target.value)}
        onFocus={() => results.length && setOpen(true)}
        onKeyDown={onKey}
      />
      {open && query.trim().length >= 2 && (
        <ul className="ss-list" role="listbox">
          {results.map((r, i) => (
            <li key={r.symbol} role="option" aria-selected={i === active}
              className={`ss-item ${i === active ? "ss-item--on" : ""}`}
              onMouseEnter={() => setActive(i)} onMouseDown={(e) => { e.preventDefault(); go(r.symbol); }}>
              <b>{r.symbol.replace(/\.NS$/, "")}</b>
              <span>{r.company_name}</span>
              <small>{r.sector}</small>
            </li>
          ))}
          {results.length === 0 && (
            <li className="ss-item ss-item--hint" onMouseDown={(e) => { e.preventDefault(); go(query); }}>
              Not in the scanner lists — press Enter to analyse <b>{query.trim().toUpperCase()}</b> as an NSE symbol
            </li>
          )}
        </ul>
      )}
    </div>
  );
}
