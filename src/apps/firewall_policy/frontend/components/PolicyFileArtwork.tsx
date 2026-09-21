import React from 'react'

/** A small spreadsheet symbol, drawn locally so it stays crisp at every scale. */
export default function PolicyFileArtwork({ selected = false }: { selected?: boolean }) {
  return <span className={`fw-file-art ${selected ? 'is-selected' : ''}`} aria-hidden="true">
    <svg viewBox="0 0 160 146" fill="none">
      <rect className="fw-paper-back" x="32" y="20" width="89" height="108" rx="12" fill="#edf2fa" stroke="#d6dfed" transform="rotate(-9 76 74)" />
      <rect x="40" y="14" width="88" height="112" rx="12" fill="white" stroke="#d6deeb" />
      <path d="M103 14v18a7 7 0 0 0 7 7h18" fill="#f0f4fa" />
      <path d="M103 14v18a7 7 0 0 0 7 7h18" stroke="#d6deeb" />
      <rect x="53" y="51" width="61" height="50" rx="4" fill="#f7faff" stroke="#cbdcf2" />
      <path d="M53 64h61M53 77h61M53 89h61M74 64v37M94 64v37" stroke="#d4e2f4" />
      <path d="M57 51h53a4 4 0 0 1 4 4v9H53v-9a4 4 0 0 1 4-4Z" fill="#dceaff" />
      <rect x="59" y="109" width="43" height="3" rx="1.5" fill="#e4eaf3" />
      <rect x="18" y="69" width="47" height="47" rx="13" fill="#1974e8" />
      <path d="m33 83 17 20m0-20-17 20" stroke="white" strokeWidth="3" strokeLinecap="round" />
      {selected && <><circle cx="120" cy="113" r="17" fill="#1974e8" stroke="white" strokeWidth="4" /><path d="m113 113 5 5 9-10" stroke="white" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round" /></>}
    </svg>
  </span>
}
