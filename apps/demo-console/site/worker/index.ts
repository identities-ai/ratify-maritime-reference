/** Cloudflare Worker entry point for the Maritime authorization console. */
import { handleImageOptimization, DEFAULT_DEVICE_SIZES, DEFAULT_IMAGE_SIZES } from "vinext/server/image-optimization";
import handler from "vinext/server/app-router-entry";

interface Fetcher {
  fetch(request: Request): Promise<Response>;
}

interface Env {
  ASSETS: Fetcher;
  LABS_ROUTER_TOKEN: string;
  CONSOLE_ALLOWED_HOSTNAMES?: string;
  IMAGES: {
    input(stream: ReadableStream): {
      transform(options: Record<string, unknown>): {
        output(options: { format: string; quality: number }): Promise<{ response(): Response }>;
      };
    };
  };
}

const PROVIDER_HOST = "ratify-maritime-lab.chuksy0x01.chatgpt.site";
const ROUTE_HEADER = "X-Ratify-Labs-Route";

function allowedHosts(configured: string | undefined): Set<string> {
  const hosts = (configured ?? PROVIDER_HOST)
    .split(",")
    .map((host) => host.trim().toLowerCase())
    .filter(Boolean);
  return new Set(hosts);
}

async function hasValidRouteCredential(request: Request, secret: string): Promise<boolean> {
  if (!secret || secret.length < 32) return false;
  const actual = request.headers.get(ROUTE_HEADER);
  if (!actual || actual.includes(",")) return false;
  const expected = `Bearer ${secret}`;
  const encoder = new TextEncoder();
  const [actualDigest, expectedDigest] = await Promise.all([
    crypto.subtle.digest("SHA-256", encoder.encode(actual)),
    crypto.subtle.digest("SHA-256", encoder.encode(expected)),
  ]);
  const actualBytes = new Uint8Array(actualDigest);
  const expectedBytes = new Uint8Array(expectedDigest);
  let difference = 0;
  for (let index = 0; index < actualBytes.length; index += 1) {
    difference |= actualBytes[index] ^ expectedBytes[index];
  }
  return difference === 0;
}

interface ExecutionContext {
  waitUntil(promise: Promise<unknown>): void;
  passThroughOnException(): void;
}

// Image security config. SVG sources with .svg extension auto-skip the
// optimization endpoint on the client side (served directly, no proxy).
// To route SVGs through the optimizer (with security headers), set
// dangerouslyAllowSVG: true in next.config.js and uncomment below:
// const imageConfig: ImageConfig = { dangerouslyAllowSVG: true };

const worker = {
  async fetch(request: Request, env: Env, ctx: ExecutionContext): Promise<Response> {
    const url = new URL(request.url);
    const local = url.hostname === "localhost" || url.hostname === "127.0.0.1";
    const routed = allowedHosts(env.CONSOLE_ALLOWED_HOSTNAMES).has(url.hostname.toLowerCase())
      && await hasValidRouteCredential(request, env.LABS_ROUTER_TOKEN);
    if (!local && !routed) {
      return new Response("Not found", {
        status: 404,
        headers: {
          "Cache-Control": "no-store",
          "Content-Security-Policy": "default-src 'none'",
        },
      });
    }

    if (url.pathname === "/maritime/_vinext/image") {
      const allowedWidths = [...DEFAULT_DEVICE_SIZES, ...DEFAULT_IMAGE_SIZES];
      return handleImageOptimization(request, {
        fetchAsset: (path) => env.ASSETS.fetch(new Request(new URL(path, request.url))),
        transformImage: async (body, { width, format, quality }) => {
          const result = await env.IMAGES.input(body).transform(width > 0 ? { width } : {}).output({ format, quality });
          return result.response();
        },
      }, allowedWidths);
    }

    return handler.fetch(request, env, ctx);
  },
};

export default worker;
