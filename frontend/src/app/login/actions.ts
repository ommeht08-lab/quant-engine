"use server";

import { cookies } from "next/headers";
import { redirect } from "next/navigation";

import { createSessionToken, verifyPassword, SESSION_COOKIE_NAME } from "@/lib/auth";

export interface LoginState {
  error?: string;
}

export async function login(_prevState: LoginState, formData: FormData): Promise<LoginState> {
  const password = formData.get("password");
  if (typeof password !== "string" || password.length === 0) {
    return { error: "Enter the dashboard password." };
  }

  let valid: boolean;
  try {
    valid = verifyPassword(password);
  } catch (error) {
    console.error("Login failed — DASHBOARD_PASSWORD misconfigured:", error);
    return { error: "This deployment is not configured for login. Contact the operator." };
  }

  if (!valid) {
    return { error: "Incorrect password." };
  }

  const cookieStore = await cookies();
  cookieStore.set(SESSION_COOKIE_NAME, createSessionToken(), {
    httpOnly: true,
    secure: process.env.NODE_ENV === "production",
    sameSite: "lax",
    path: "/",
  });

  redirect("/");
}
