import { Brand } from "@/components/brand";
import { Card } from "@/components/ui";
import { messages } from "@/lib/messages";
import { LoginForm } from "./login-form";

export default function LoginPage() {
  return (
    <main className="grid min-h-screen place-items-center px-4 py-10">
      <Card className="w-full max-w-sm p-8">
        <Brand />
        <h1 className="mt-8 text-2xl font-bold tracking-tight">{messages.login.title}</h1>
        <LoginForm />
      </Card>
    </main>
  );
}
