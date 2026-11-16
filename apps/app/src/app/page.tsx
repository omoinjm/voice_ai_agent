import Link from "next/link";
import { config } from "@/lib/config";

const STEPS = [
  ["Register", "Tell us about your business and upload anything that explains your services and prices."],
  ["Get approved", "We review your details, with AI checking nothing important is missing."],
  ["Connect WhatsApp", "Link your WhatsApp Business number so customers can call it."],
  ["Meet your agent", "Name it, pick a voice (or use your own), and we set it up and test it for you."],
];

export default function Home() {
  return (
    <main className="mx-auto max-w-4xl px-4 py-16">
      <h1 className="text-3xl font-semibold sm:text-4xl">{config.productName}</h1>
      <p className="mt-4 max-w-2xl text-lg text-muted">
        A voice agent that answers your business&apos;s WhatsApp calls, books appointments into your calendar and
        sends customers a payment link, or lets them pay when they arrive.
      </p>
      <div className="mt-8 flex gap-3">
        <Link href="/login" className="rounded bg-accent px-4 py-2 font-medium text-white hover:opacity-90">
          Register your business
        </Link>
        <Link href="/login" className="rounded border border-border px-4 py-2 hover:bg-surface">
          Sign in
        </Link>
      </div>
      <ol className="mt-14 grid gap-4 sm:grid-cols-2">
        {STEPS.map(([title, text], i) => (
          <li key={title} className="rounded-lg border border-border p-4">
            <div className="text-sm text-muted">Step {i + 1}</div>
            <div className="mt-1 font-medium">{title}</div>
            <p className="mt-1 text-sm text-muted">{text}</p>
          </li>
        ))}
      </ol>
    </main>
  );
}
