"use client";
import {useEffect,useRef,useState} from "react";
import {usePathname} from "next/navigation";
import Link from "next/link";
import SearchBar from "@/components/SearchBar";
import styles from "./AppHeader.module.css";
const items=[
 ['/workspace','Overview','dashboard'],['/workspace/valuation','Valuation','chart'],['/workspace/cash-flows','Cash flow forecast','chart'],['/workspace/projections','Projection detail','table'],['/research/msft','MSFT Study','study'],['/workspace/evidence','Evidence & sources','study'],['/workspace/assumptions','Model assumptions','settings']
] as const;
function Icon({name}:{name:string}){const paths:Record<string,string>={dashboard:'M3 3h7v7H3z M14 3h7v7h-7z M3 14h7v7H3z M14 14h7v7h-7z',chart:'M4 4v16h16 M8 16v-5 M13 16V7 M18 16V4',table:'M3 4h18v16H3z M3 10h18 M10 4v16',portfolio:'M3 8h18v12H3z M8 8V4h8v4 M3 13h18',trades:'M4 7h16l-4-4 M20 17H4l4 4',study:'M4 3h7v18H4z M13 3h7v18h-7z',settings:'M3 6h18 M3 12h18 M3 18h18 M8 3v6 M16 9v6 M10 15v6',lock:'M5 10h14v11H5z M8 10V6a4 4 0 0 1 8 0v4',menu:'M4 6h16 M4 12h16 M4 18h16',close:'M6 6l12 12 M6 18 18 6'};return <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.7" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true"><path d={paths[name]??paths.chart}/></svg>}
export default function AppHeader(){
 const pathname=usePathname(),dialog=useRef<HTMLDialogElement>(null),trigger=useRef<HTMLButtonElement>(null),[open,setOpen]=useState(false);
 useEffect(()=>{dialog.current?.close();},[pathname]);
 useEffect(()=>{const m=window.matchMedia('(min-width: 821px)');const close=()=>{if(m.matches)dialog.current?.close()};m.addEventListener('change',close);return()=>m.removeEventListener('change',close)},[]);
 if(pathname==='/login'||pathname==='/methodology'||pathname.startsWith('/methodology/')||pathname==='/research'||pathname.startsWith('/research/'))return null;
 const brand=<Link href="/workspace" className={styles.brand}><span className={styles.brandMark}><Icon name="chart"/></span><strong>Valuation Engine</strong></Link>;
 const nav=<nav className={styles.nav} aria-label="Main navigation">{items.map(([href,label,icon])=><Link key={href} href={href} aria-current={pathname===href?'page':undefined} title={href==='/research/msft'?'Archived MSFT sensitivity study, separate from live valuation':undefined} className={styles.navLink}><Icon name={icon}/><span>{label}</span></Link>)}</nav>;
 return <div className={styles.chrome} data-app-chrome>
  <aside className={styles.sidebar}>{brand}<p className={styles.navLabel}>Workspace</p>{nav}<div className={styles.sidebarNote}><strong>Equity research workspace</strong><p>Company fundamentals, valuation assumptions and source evidence.</p></div></aside>
  <header className={styles.topbar}><button ref={trigger} className={styles.menuButton} aria-label="Open navigation" aria-expanded={open} aria-controls="workspace-navigation" onClick={()=>{dialog.current?.showModal();setOpen(true)}}><Icon name="menu"/></button><SearchBar className={styles.search}/><div className={styles.topMeta}><span className={styles.avatar}>OM</span><span>Om Mehta</span></div></header>
  <dialog id="workspace-navigation" ref={dialog} className={styles.drawer} aria-label="Workspace navigation" onClose={()=>{setOpen(false);trigger.current?.focus()}} onClick={event=>{if(event.target===dialog.current)dialog.current.close()}}><div className={styles.drawerHeading}>{brand}<button className={styles.closeButton} aria-label="Close navigation" onClick={()=>dialog.current?.close()}><Icon name="close"/></button></div>{nav}</dialog>
 </div>;
}
