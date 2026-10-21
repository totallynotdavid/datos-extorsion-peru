import { cpSync, createReadStream, existsSync, statSync } from "node:fs";
import { join, resolve } from "node:path";
import { defineConfig, type Plugin } from "vite";

function serveFixtures(): Plugin {
  const dir = resolve(import.meta.dirname, "fixtures");
  return {
    name: "serve-fixtures",
    configureServer(server) {
      server.middlewares.use("/fixtures", (req, res, next) => {
        const file = join(dir, decodeURIComponent(req.url ?? ""));
        if (
          req.url &&
          file.startsWith(dir) &&
          existsSync(file) &&
          statSync(file).isFile()
        ) {
          createReadStream(file).pipe(res);
        } else {
          next();
        }
      });
    },
    closeBundle() {
      cpSync(dir, resolve(import.meta.dirname, "dist/fixtures"), {
        recursive: true,
      });
    },
  };
}

export default defineConfig({
  base: "./",
  plugins: [serveFixtures()],
});
