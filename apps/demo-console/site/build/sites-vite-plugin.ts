import { access, cp, mkdir, readdir, rm } from "node:fs/promises";
import { resolve } from "node:path";
import type { Plugin } from "vite";

// `basePath` prefixes every asset URL the app requests, but public files are
// emitted at the client root. Mirroring them under the base path keeps
// `/maritime/ratify-logo.png` resolvable; without it the host answers an
// unmatched path with an empty 200 and the image silently fails to render.
async function mirrorPublicAssetsUnderBasePath(
  root: string, basePath: string,
): Promise<void> {
  const source = resolve(root, "public");
  if (!basePath || !(await exists(source))) return;
  const destination = resolve(root, "dist", "client", basePath);
  for (const entry of await readdir(source, { withFileTypes: true })) {
    await cp(
      resolve(source, entry.name),
      resolve(destination, entry.name),
      { recursive: true },
    );
  }
}

async function exists(path: string): Promise<boolean> {
  try {
    await access(path);
    return true;
  } catch (error) {
    if ((error as NodeJS.ErrnoException).code === "ENOENT") {
      return false;
    }
    throw error;
  }
}

// Packages Sites metadata and migrations after Vite finishes compiling.
export function sites(): Plugin {
  let root = process.cwd();

  return {
    name: "sites",
    apply: "build",
    configResolved(config) {
      root = config.root;
    },
    async closeBundle() {
      await mirrorPublicAssetsUnderBasePath(root, "maritime");

      const outputDirectory = resolve(root, "dist", ".openai");
      const hostingConfig = resolve(root, ".openai", "hosting.json");
      const drizzleSource = resolve(root, "drizzle");

      await rm(outputDirectory, { recursive: true, force: true });
      await mkdir(outputDirectory, { recursive: true });

      if (await exists(hostingConfig)) {
        await cp(hostingConfig, resolve(outputDirectory, "hosting.json"));
      }
      if (await exists(drizzleSource)) {
        await cp(drizzleSource, resolve(outputDirectory, "drizzle"), {
          recursive: true,
        });
      }
    },
  };
}
