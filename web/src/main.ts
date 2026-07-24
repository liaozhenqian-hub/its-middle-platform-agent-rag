import "element-plus/dist/index.css";
import "./styles/index.css";

import ElementPlus from "element-plus";
import zhCn from "element-plus/es/locale/lang/zh-cn";
import { createApp } from "vue";

import App from "./App.vue";
import { router } from "./router";
import { pinia } from "./stores/pinia";

createApp(App).use(pinia).use(router).use(ElementPlus, { locale: zhCn }).mount("#app");
