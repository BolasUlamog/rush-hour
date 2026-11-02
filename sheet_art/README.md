# Printed-sheet artwork

Drawn onto every answer sheet by `packet_pdf.py`. Vercel only bundles what
`vercel.json`'s `includeFiles` names, so this folder is listed there; without it
sheet building fails on the live site.

| file | from |
| --- | --- |
| `meet-badge.png` | `public/assets/sm3-logo.jpg`, the Stanford Mini Math Meet badge |
| `fossil-triceratops.png` | the triceratops skull, top-left of the meet's fossil-border illustration |
| `fossil-trex.png` | the T. rex skull, bottom-right of the same illustration |

The skulls were cut out by their pink fossil blob, so leaves and vines that only
touch the outline were left behind.

**Before adding artwork, know where the scanner looks for corner marks**: the
outer 26% of the page width by 20% of its height at each corner. In there, a
solid dark shape between roughly half and twice the size of a corner mark can be
taken for one, and a dark shape touching a mark merges with it. Line art, text
and light fills are fine; keep solid maroon blobs out. The band beside the board
is outside every window, which is why the skulls sit there.
