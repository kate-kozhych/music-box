import type { StorybookConfig } from "@storybook/react-vite";

// Every shared UI component gets stories (tech-stack.md §3); first ones in B-32.
const config: StorybookConfig = {
  stories: ["../src/**/*.stories.@(ts|tsx)"],
  addons: ["@storybook/addon-a11y"],
  framework: { name: "@storybook/react-vite", options: {} },
};

export default config;
