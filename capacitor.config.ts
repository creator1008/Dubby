import type { CapacitorConfig } from "@capacitor/cli";

const config: CapacitorConfig = {
  appId: "com.dubby.app",
  appName: "Dubby",
  webDir: "out",
  server: {
    androidScheme: "https",
    // Optional: load the live Pages site in the native shell for device testing.
    // Example: CAPACITOR_SERVER_URL=https://creator1008.github.io/Dubby
    ...(process.env.CAPACITOR_SERVER_URL
      ? { url: process.env.CAPACITOR_SERVER_URL }
      : {}),
  },
  ios: {
    contentInset: "automatic",
  },
  android: {
    allowMixedContent: false,
  },
};

export default config;
