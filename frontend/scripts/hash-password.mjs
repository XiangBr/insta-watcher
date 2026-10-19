// Prints the argon2id hash to put in ADMIN_PASSWORD_HASH. The password is read without echo
// and is never written anywhere. `pnpm hash-password`
import { hash } from "@node-rs/argon2";

const MIN_LENGTH = 12;

function readHidden(prompt) {
  return new Promise((resolve, reject) => {
    const { stdin, stdout } = process;
    if (!stdin.isTTY) {
      // Piped input, e.g. from a password manager: take everything up to the newline.
      let data = "";
      stdin.setEncoding("utf8");
      stdin.on("data", (chunk) => (data += chunk));
      stdin.on("end", () => resolve(data.split(/\r?\n/)[0] ?? ""));
      stdin.on("error", reject);
      return;
    }
    stdout.write(prompt);
    let value = "";
    stdin.setRawMode(true);
    stdin.resume();
    stdin.setEncoding("utf8");
    const onData = (chunk) => {
      for (const char of chunk) {
        if (char === "\r" || char === "\n") {
          stdin.setRawMode(false);
          stdin.pause();
          stdin.off("data", onData);
          stdout.write("\n");
          resolve(value);
          return;
        }
        if (char === "\u0003") {
          stdin.setRawMode(false);
          stdout.write("\n");
          process.exit(130);
        }
        if (char === "\u007f" || char === "\b") value = value.slice(0, -1);
        else value += char;
      }
    };
    stdin.on("data", onData);
  });
}

const password = await readHidden("Admin password (input hidden): ");
if (process.stdin.isTTY) {
  const again = await readHidden("Repeat it: ");
  if (again !== password) {
    console.error("The two entries differ.");
    process.exit(1);
  }
}
if (password.length < MIN_LENGTH) {
  console.error(`Use at least ${MIN_LENGTH} characters.`);
  process.exit(1);
}

// The library defaults are argon2id with the OWASP minimum cost.
const encoded = await hash(password);
// scripts/setup.sh reads the first line, so it stays the plain value.
console.log("\nVercel (Settings > Environment Variables), paste as is:");
console.log(`ADMIN_PASSWORD_HASH=${encoded}`);
console.log("\n.env for Docker Compose, in single quotes:");
console.log(`ADMIN_PASSWORD_HASH='${encoded}'`);
console.log("\n.env.local for pnpm dev, where every $ must be escaped:");
console.log(`ADMIN_PASSWORD_HASH=${encoded.replaceAll("$", "\\$")}`);
