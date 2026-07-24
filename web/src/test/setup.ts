import { config } from "@vue/test-utils";

config.global.stubs = {
  transition: false,
  "el-icon": true,
};

Object.defineProperty(window, "matchMedia", {
  configurable: true,
  value: () => ({
    matches: false,
    addEventListener: () => undefined,
    removeEventListener: () => undefined,
  }),
});
