<script setup lang="ts">
import { ArrowLeft, Lock, User } from "@element-plus/icons-vue";
import { reactive } from "vue";
import { useRoute, useRouter } from "vue-router";

import { useAuthStore } from "@/stores/auth";

const auth = useAuthStore();
const route = useRoute();
const router = useRouter();
const form = reactive({ username: "", password: "" });

async function submit() {
  if (!form.username.trim() || !form.password) return;
  try {
    await auth.login(form.username.trim(), form.password);
    const redirect = typeof route.query.redirect === "string" ? route.query.redirect : "/admin";
    await router.replace(redirect);
  } finally {
    form.password = "";
  }
}
</script>

<template>
  <main class="login-page">
    <router-link class="back-link" to="/chat">
      <el-icon><ArrowLeft /></el-icon>
      返回工作台
    </router-link>
    <section class="login-panel" aria-labelledby="login-title">
      <div class="login-mark"><Lock /></div>
      <div class="login-heading">
        <p>中台知识工作台</p>
        <h1 id="login-title">管理员登录</h1>
      </div>
      <el-alert v-if="auth.error" :title="auth.error" type="error" :closable="false" show-icon />
      <form @submit.prevent="submit">
        <el-form-item label="账号">
          <el-input
            v-model="form.username"
            data-testid="username"
            autocomplete="username"
            :prefix-icon="User"
          />
        </el-form-item>
        <el-form-item label="密码">
          <el-input
            v-model="form.password"
            data-testid="password"
            type="password"
            autocomplete="current-password"
            show-password
            :prefix-icon="Lock"
          />
        </el-form-item>
        <el-button
          type="primary"
          native-type="submit"
          :loading="auth.loading"
          :disabled="!form.username.trim() || !form.password"
        >
          登录
        </el-button>
      </form>
    </section>
  </main>
</template>

<style scoped>
.login-page {
  display: grid;
  place-items: center;
  min-height: 100dvh;
  padding: 64px 20px 40px;
  background: var(--surface-soft);
}

.back-link {
  position: fixed;
  top: 20px;
  left: 24px;
  display: inline-flex;
  align-items: center;
  gap: 6px;
  color: var(--text-secondary);
  font-size: 13px;
  text-decoration: none;
}

.login-panel {
  width: min(100%, 380px);
  padding: 28px;
  background: var(--surface-raised);
  border: 1px solid var(--border-subtle);
  border-radius: 8px;
  box-shadow: 0 12px 32px rgba(31, 42, 55, 0.08);
}

.login-mark {
  display: grid;
  place-items: center;
  width: 38px;
  height: 38px;
  color: #fff;
  background: var(--accent-strong);
  border-radius: 7px;
}

.login-mark svg {
  width: 19px;
}

.login-heading {
  margin: 18px 0 24px;
}

.login-heading p {
  margin: 0 0 4px;
  color: var(--text-muted);
  font-size: 12px;
}

h1 {
  margin: 0;
  font-size: 22px;
  letter-spacing: 0;
}

form {
  display: grid;
  margin-top: 20px;
}

form > .el-button {
  width: 100%;
  margin-top: 6px;
}

@media (max-width: 520px) {
  .login-page {
    place-items: stretch;
    align-content: center;
    padding: 64px 16px 24px;
  }

  .back-link {
    left: 16px;
  }

  .login-panel {
    width: auto;
    padding: 24px 20px;
  }
}
</style>
