import type { NextConfig } from "next";

// Static export: the landing page has no server work to do, and a bucket + CDN is a
// cheaper and more available thing to run than a Node process. Drop `output` when the
// authenticated app (artists, generation, review) lands alongside it.
const nextConfig: NextConfig = {
  output: "export",
  images: { unoptimized: true },
};

export default nextConfig;
