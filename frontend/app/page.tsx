import { Nav } from "@/components/landing/Nav";
import { Hero } from "@/components/landing/Hero";
import { LiveStats } from "@/components/landing/LiveStats";
import { Features } from "@/components/landing/Features";
import { HowYouUse } from "@/components/landing/HowYouUse";
import { Architecture } from "@/components/landing/Architecture";
import { DemoVideo } from "@/components/landing/DemoVideo";
import { Thesis } from "@/components/landing/Thesis";
import { Footer } from "@/components/landing/Footer";

export default function HomePage() {
  return (
    <main className="min-h-screen bg-slate-950 text-slate-100">
      <Nav />
      <Hero />
      <LiveStats />
      <Features />
      <HowYouUse />
      <Architecture />
      <DemoVideo />
      <Thesis />
      <Footer />
    </main>
  );
}
