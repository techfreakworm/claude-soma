import NextAuth from "next-auth";
import GitHub from "next-auth/providers/github";

const ALLOWED = (process.env.HERMES_ALLOWED_GITHUB_HANDLES || "")
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
      // Put githubHandle on session.user (NOT session) — middleware reads it
      // via req.auth.user.githubHandle. Putting it at the top level made
      // middleware see undefined and bounce every request back to signin.
      if (session.user) {
        (session.user as { githubHandle?: string }).githubHandle =
          token.githubHandle as string | undefined;
      }
      return session;
    },
    async jwt({ token, profile }) {
      const handle = (profile as { login?: string } | undefined)?.login;
      if (handle) token.githubHandle = handle;
      return token;
    },
  },
});
