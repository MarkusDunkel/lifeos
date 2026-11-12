import { createFileRoute, Outlet, redirect } from "@tanstack/react-router";
import { AppShell } from "../components/AppShell";
import { sessionQuery } from "../lib/session";

export const Route = createFileRoute("/_app")({
  beforeLoad: async ({ context, location }) => {
    const session = await context.queryClient.ensureQueryData(sessionQuery);
    if (!session.authenticated) {
      throw redirect({ to: "/login", search: { redirect: location.href } });
    }
  },
  component: () => (
    <AppShell>
      <Outlet />
    </AppShell>
  ),
});
