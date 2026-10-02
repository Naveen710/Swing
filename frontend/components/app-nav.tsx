"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";

import { useJournal, useWatchlist } from "../lib/store";
import { StockSearch } from "./stock-search";

export function AppNav() {
  const pathname = usePathname();
  const [journal] = useJournal();
  const [watchlist] = useWatchlist();
  const open = journal.filter((t) => t.status === "open").length;

  const links = [
    { href: "/", label: "Scanner" },
    { href: "/watchlist", label: "Watchlist", count: watchlist.length },
    { href: "/journal", label: "Journal", count: open },
    { href: "/valuation", label: "Valuation" },
    { href: "/performance", label: "Performance" },
  ];

  return (
    <nav className="app-nav">
      <span className="app-nav-brand">NSE Swing</span>
      <StockSearch />
      <div className="app-nav-links">
        {links.map((l) => (
          <Link key={l.href} href={l.href}
            className={`app-nav-link ${pathname === l.href || (l.href !== "/" && pathname?.startsWith(l.href + "/")) ? "app-nav-link--on" : ""}`}>
            {l.label}
            {l.count ? <span className="app-nav-count">{l.count}</span> : null}
          </Link>
        ))}
      </div>
    </nav>
  );
}
