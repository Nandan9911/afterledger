import type { NextConfig } from 'next';
import createNextIntlPlugin from 'next-intl/plugin';

const withNextIntl = createNextIntlPlugin('./src/i18n/request.ts');

const nextConfig: NextConfig = {
  output: 'standalone',
  reactStrictMode: true,
  poweredByHeader: false,
  transpilePackages: ['@afterledger/contracts'],
  // The API's page images and PDFs are served by the API itself, not optimised here.
  images: { unoptimized: true },
  // no development badge on any page, in development or in production
  devIndicators: false,
  // A phone on the same Wi-Fi opens the dev server by the laptop's private address (http://192.168.1.23:3000).
  allowedDevOrigins: ['192.168.*.*', '10.*.*.*', '172.*.*.*'],
};

export default withNextIntl(nextConfig);
