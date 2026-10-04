import assert from "node:assert/strict";
import test from "node:test";
import { isPasswordlessLocalRequest } from "./local-access.ts";

test("explicit local development access accepts loopback hosts", () => {
  const env = { NODE_ENV: "development", LOCAL_PASSWORDLESS: "1" };
  for (const host of ["localhost", "localhost:3420", "127.0.0.1:3420", "[::1]:3420"]) {
    assert.equal(isPasswordlessLocalRequest(host, env), true);
  }
});

test("production, tests, and ordinary development keep session protection", () => {
  for (const env of [
    { NODE_ENV: "production", LOCAL_PASSWORDLESS: "1" },
    { NODE_ENV: "test", LOCAL_PASSWORDLESS: "1" },
    { NODE_ENV: "development" },
    { NODE_ENV: "development", LOCAL_PASSWORDLESS: "0" },
  ]) {
    assert.equal(isPasswordlessLocalRequest("127.0.0.1:3420", env), false);
  }
});

test("external hosts and lookalikes cannot opt out of authentication", () => {
  const env = { NODE_ENV: "development", LOCAL_PASSWORDLESS: "1" };
  for (const host of [null, "", "example.com", "localhost.example.com", "127.0.0.1.example.com", "127.0.0.2", "localhost@evil.com", "localhost:3420/evil"]) {
    assert.equal(isPasswordlessLocalRequest(host, env), false);
  }
});
