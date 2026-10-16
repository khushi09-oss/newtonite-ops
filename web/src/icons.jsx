// A dozen 16px line icons, drawn on one grid so they sit together. No icon library for this few.
const paths = {
  dashboard: "M2.5 2.5h4.5v4.5H2.5zM9 2.5h4.5v7H9zM2.5 9h4.5v4.5H2.5zM9 11.5h4.5v2H9z",
  inbox: "M2.5 9.5 4 3.5h8l1.5 6M2.5 9.5v3.5h11V9.5M2.5 9.5h3.5l.5 1.5h3l.5-1.5h3.5",
  list: "M5.5 4h8M5.5 8h8M5.5 12h8M2.5 4h.01M2.5 8h.01M2.5 12h.01",
  bell: "M4 11V7a4 4 0 0 1 8 0v4l1 1.5H3zM6.5 13.5a1.5 1.5 0 0 0 3 0",
  shield: "M8 1.8 13 3.6v4.1c0 3-2.1 5.4-5 6.5-2.9-1.1-5-3.5-5-6.5V3.6z",
  plus: "M8 3v10M3 8h10",
  search: "M7 12a5 5 0 1 0 0-10 5 5 0 0 0 0 10zM10.6 10.6 14 14",
  out: "M6 2.5H3v11h3M10 5l3 3-3 3M13 8H6",
  chevron: "M4 6l4 4 4-4",
  arrow: "M3 8h10M9 4l4 4-4 4",
  clock: "M8 14A6 6 0 1 0 8 2a6 6 0 0 0 0 12zM8 4.5V8l2.5 1.5",
  users: "M6 8a2.5 2.5 0 1 0 0-5 2.5 2.5 0 0 0 0 5zM1.5 13.5c.4-2.3 2.2-3.5 4.5-3.5s4.1 1.2 4.5 3.5M11 3.2a2.5 2.5 0 0 1 0 4.6M12 10.1c1.3.5 2.2 1.7 2.5 3.4",
};

export function Icon({ name, size = 16, className = "" }) {
  return (
    <svg
      className={`icon ${className}`}
      width={size}
      height={size}
      viewBox="0 0 16 16"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.5"
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
    >
      <path d={paths[name]} />
    </svg>
  );
}
