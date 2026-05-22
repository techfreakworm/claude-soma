import NextAuth from "next-auth";
import GitHub from "next-auth/providers/github";

const ALLOWED = (process.env.HERMES_ALLOWED_GITHUB_HANDLES || "techfreakworm")
  .split(",")
  .map((s) => s.trim())
  .filter(Boolean);

export const { handlers, signIn, signOut, auth } = NextAuth({
  providers: [GitHub],
  callbacks: {
    async signIn({ profile }) {
      const handle = (profile as { login?: string })?.login;
      return !!handle && ALLOWED.includes(handle);
    },
    async session({ session, token }) {
      (session as { githubHandle?: string }).githubHandle = token.githubHandle as string | undefined;
      return session;
    },
    async jwt({ token, profile }) {
      const handle = (profile as { login?: string } | undefined)?.login;
      if (handle) token.githubHandle = handle;
      return token;
    },
  },
});
