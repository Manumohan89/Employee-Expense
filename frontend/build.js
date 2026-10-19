const fs = require("node:fs");
const path = require("node:path");

const sourceDir = __dirname;
const outputDir = path.join(sourceDir, "dist");
const configuredApiUrl = process.env.API_URL?.trim();
const apiUrl = configuredApiUrl || (process.env.VERCEL ? "" : "http://127.0.0.1:5000");

if (!apiUrl) {
  throw new Error("Set API_URL in the Vercel project environment variables.");
}

let parsedApiUrl;
try {
  parsedApiUrl = new URL(apiUrl);
} catch {
  throw new Error("API_URL must be a valid absolute HTTP or HTTPS URL.");
}
if (!["http:", "https:"].includes(parsedApiUrl.protocol)) {
  throw new Error("API_URL must use HTTP or HTTPS.");
}

const normalizedApiUrl = apiUrl.replace(/\/+$/, "");
const excludedDirectories = new Set([".vercel", "dist", "node_modules"]);
const excludedFiles = new Set(["build.js", "package.json", "package-lock.json"]);

fs.rmSync(outputDir, { recursive: true, force: true });
for (const entry of fs.readdirSync(sourceDir, { withFileTypes: true })) {
  const name = entry.name;
  if (excludedDirectories.has(name) || excludedFiles.has(name) || name.startsWith(".env")) continue;
  fs.cpSync(path.join(sourceDir, name), path.join(outputDir, name), {
    recursive: true,
    filter(source) {
      const relative = path.relative(sourceDir, source);
      if (relative.split(path.sep).some((part) => excludedDirectories.has(part))) return false;
      const sourceName = path.basename(source);
      return !excludedFiles.has(sourceName) && !sourceName.startsWith(".env");
    }
  });
}
fs.writeFileSync(
  path.join(outputDir, "js", "config.js"),
  `window.APP_CONFIG = { API_URL: ${JSON.stringify(normalizedApiUrl)} };\n`
);
