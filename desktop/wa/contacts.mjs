// Contact lookup: contacts are {name, jid, number:"+91…"}.
const words = (s) => s.toLowerCase().split(/[^\p{L}\p{N}']+/u).filter(Boolean);

export function match(contacts, q) {
  q = String(q ?? "").trim().toLowerCase();
  if (!q) return [];
  const digits = q.replace(/\D/g, "");
  if (digits.length >= 3 && /^[\d\s+\-()]+$/.test(q)) {
    return contacts.filter((c) => c.number.replace(/\D/g, "").includes(digits));
  }
  const exact = contacts.filter((c) => (c.name ?? "").toLowerCase() === q);
  if (exact.length) return exact;
  const qw = words(q);
  return contacts.filter((c) => {
    const nw = words(c.name ?? "");
    return qw.every((w) => nw.some((n) => n.startsWith(w)));
  });
}
