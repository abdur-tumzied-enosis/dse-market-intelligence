/** @type {import('next').NextConfig} */
const nextConfig = {
  async rewrites() {
    return [
      {
        source: "/api/mgmt/:path*",
        destination: `${process.env.MGMT_API_URL || "http://localhost:8001"}/mgmt/:path*`,
      },
    ];
  },
};

module.exports = nextConfig;
