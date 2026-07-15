const { get } = require("@vercel/blob");

module.exports = async function handler(request, response) {
  try {
    const result = await get("data/dashboard.json", { access: "private" });
    if (!result || result.statusCode !== 200) {
      response.status(503).json({ error: "Dashboard data has not been synchronized yet." });
      return;
    }
    const chunks = [];
    for await (const chunk of result.stream) chunks.push(chunk);
    const data = JSON.parse(Buffer.concat(chunks).toString("utf8"));
    response.setHeader("Cache-Control", "no-store, max-age=0");
    response.status(200).json(data);
  } catch (error) {
    response.status(500).json({ error: "Unable to load dashboard data.", detail: error.message });
  }
};
