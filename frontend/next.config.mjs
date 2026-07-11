/** @type {import('next').NextConfig} */
const defaultApiProxyTarget =
  process.env.NODE_ENV === "production"
    ? "https://naveen710-swing-api.onrender.com/api"
    : "http://localhost:8000/api";

const apiProxyTarget = (process.env.API_PROXY_TARGET || defaultApiProxyTarget).replace(
  /\/+$/,
  ""
);

const nextConfig = {
  reactStrictMode: true,
  async rewrites() {
    return [
      {
        source: "/api-proxy/:path*",
        destination: `${apiProxyTarget}/:path*`
      }
    ];
  }
};

export default nextConfig;
