const { get } = require("@vercel/blob");

module.exports = async function handler(request, response) {
  try {
    const result = await get("data/future-room-prices.json", { access: "private" });
    if (!result || result.statusCode !== 200) {
      response.status(503).json({ error: "Future price sheet has not been synchronized yet." });
      return;
    }
    const chunks = [];
    for await (const chunk of result.stream) chunks.push(chunk);
    const body = Buffer.concat(chunks);
    response.setHeader("Cache-Control", "no-store, max-age=0");
    response.setHeader("Content-Type", "application/json; charset=utf-8");
    response.setHeader("Content-Disposition", "attachment; filename=ctrip-future-room-prices.json");
    response.status(200).send(body);
  } catch (error) {
    response.status(500).json({ error: "Unable to load future price sheet.", detail: error.message });
  }
};
