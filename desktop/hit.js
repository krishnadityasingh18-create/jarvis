const over = (p, b, r, pad = 8) => !!r && p.x >= b.x + r.x - pad && p.x <= b.x + r.x + r.w + pad &&
  p.y >= b.y + r.y - pad && p.y <= b.y + r.y + r.h + pad;
module.exports = { over };
