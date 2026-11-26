import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { createRouter, RouterProvider } from "@tanstack/react-router";
import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import "./index.css";
import { ApiError, UNAUTHORIZED_EVENT } from "./lib/api";
import { routeTree } from "./routeTree.gen";

const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      retry: (count, error) => !(error instanceof ApiError && error.status < 500) && count < 2,
      refetchOnWindowFocus: true,
    },
  },
});

const router = createRouter({
  routeTree,
  context: { queryClient },
  defaultPreload: "intent",
  defaultPreloadStaleTime: 0,
  scrollRestoration: true,
  // pages scroll inside the shell's <main>, not the window (see AppShell)
  scrollToTopSelectors: ["#main"],
});

declare module "@tanstack/react-router" {
  interface Register {
    router: typeof router;
  }
}

// Any 401 from the API (e.g. expired session) sends the user to the login page.
window.addEventListener(UNAUTHORIZED_EVENT, () => {
  queryClient.setQueryData(["session"], { authenticated: false, auth_disabled: false });
  if (router.state.location.pathname !== "/login") {
    router.navigate({ to: "/login", search: { redirect: router.state.location.href } });
  }
});

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <QueryClientProvider client={queryClient}>
      <RouterProvider router={router} />
    </QueryClientProvider>
  </StrictMode>,
);
