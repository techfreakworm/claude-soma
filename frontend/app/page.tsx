import { Hero } from "@/components/landing/Hero";
import { LiveStats } from "@/components/landing/LiveStats";
import { Architecture } from "@/components/landing/Architecture";
import { Thesis } from "@/components/landing/Thesis";
import { DemoVideo } from "@/components/landing/DemoVideo";
import { Footer } from "@/components/landing/Footer";

export default function HomePage() {
  return (
    <main className="min-h-screen bg-slate-950 text-slate-100">
      <Hero />
      <LiveStats />
      <Architecture />
      <Thesis />
      <DemoVideo />
      <Footer />
    </main>
  );
}
