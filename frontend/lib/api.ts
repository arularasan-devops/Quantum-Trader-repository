export const API_BASE =
  process.env.NEXT_PUBLIC_API_BASE || "http://localhost:8000";
export const WS_BASE =
  process.env.NEXT_PUBLIC_WS_BASE || "ws://localhost:8000";

export async function buyOption(option_symbol: string, lots = 1) {
  const res = await fetch(`${API_BASE}/api/buy`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ option_symbol, lots }),
  });
  return res.json();
}

export async function sellPosition() {
  const res = await fetch(`${API_BASE}/api/sell`, { method: "POST" });
  return res.json();
}
