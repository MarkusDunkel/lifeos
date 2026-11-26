import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Presentation, RefreshCw } from "lucide-react";
import { useEffect, useState } from "react";
import { type DeviceCode, disconnect, pollConnect, presentationsStatusQuery, startConnect, syncPresentations } from "./api";
import { summarize } from "../mirrors/mirror";
import { buttonClass, cardClass, QueryState } from "../../components/ui";

const btn =
  buttonClass("secondary");

const OUTCOME = {
  connected: "Connected.",
  expired: "The code expired, please try again.",
  denied: "Sign-in was declined.",
} as const;

/** Settings section: connect Microsoft (device code) and sync the presentations folder. */
export function PresentationsSettings() {
  const queryClient = useQueryClient();
  const status = useQuery(presentationsStatusQuery);
  const [code, setCode] = useState<DeviceCode | null>(null);
  const [message, setMessage] = useState<string | null>(null);

  const connect = useMutation({
    mutationFn: startConnect,
    onSuccess: (c) => {
      setCode(c);
      setMessage(null);
    },
  });
  const sync = useMutation({
    mutationFn: syncPresentations,
    onSettled: () => queryClient.invalidateQueries({ queryKey: ["presentations"] }).then(() =>
      queryClient.invalidateQueries({ queryKey: ["knowledge"] }),
    ),
  });

  // While a code is shown, ask the backend whether the sign-in has happened.
  useEffect(() => {
    if (!code) return;
    const timer = window.setInterval(async () => {
      try {
        const { status: s } = await pollConnect();
        if (s === "pending") return;
        setCode(null);
        setMessage(OUTCOME[s]);
      } catch (e) {
        setCode(null);
        setMessage(e instanceof Error ? e.message : String(e));
      }
      await queryClient.invalidateQueries({ queryKey: ["presentations"] });
    }, Math.max(code.interval, 3) * 1000);
    return () => window.clearInterval(timer);
  }, [code, queryClient]);

  const s = status.data;
  return (
    <section className={`${cardClass} space-y-3 p-4`}>
      <h2 className="flex items-center gap-2 font-semibold">
        <Presentation className="size-5 text-orange-600" /> Presentations (OneDrive)
      </h2>
      <p className="text-sm text-slate-500">
        The text of every PowerPoint in a OneDrive folder is mirrored into the knowledge hub, with a link that opens the
        presentation in PowerPoint for the web. LifeOS only reads from OneDrive.
      </p>
      {!s ? (
        <QueryState query={status} />
      ) : !s.configured ? (
        <p className="text-sm text-amber-600">
          Not set up. Add {s.missing.join(", ")} to the deployment's .env (see docs/vault.md).
        </p>
      ) : (
        <>
          <dl className="grid grid-cols-[auto_1fr] gap-x-4 gap-y-1 text-sm">
            <dt className="text-slate-500">Account</dt>
            <dd>{s.connected ? (s.account ?? "connected") : "not connected"}</dd>
            <dt className="text-slate-500">OneDrive folder</dt>
            <dd>{s.folder}</dd>
            <dt className="text-slate-500">Mirrored to</dt>
            <dd>
              knowledge / {s.target} ({s.presentations} presentations)
            </dd>
            <dt className="text-slate-500">Last sync</dt>
            <dd>{s.last ? `${new Date(s.last.synced_at).toLocaleString()} · ${summarize(s.last)}` : "never"}</dd>
          </dl>
          {code && (
            <div className="rounded-lg bg-accent-50 p-3 text-sm dark:bg-accent-950">
              Open{" "}
              <a href={code.verification_uri} target="_blank" rel="noopener noreferrer" className="text-accent-600 underline">
                {code.verification_uri}
              </a>{" "}
              and enter the code{" "}
              <code className="rounded bg-white px-1.5 py-0.5 font-mono text-base font-semibold dark:bg-slate-900">
                {code.user_code}
              </code>
              . Waiting for the sign-in…
            </div>
          )}
          <div className="flex flex-wrap gap-2">
            {s.connected ? (
              <>
                <button className={btn} onClick={() => sync.mutate()} disabled={sync.isPending}>
                  <RefreshCw className={`size-4 ${sync.isPending ? "animate-spin" : ""}`} /> Sync presentations
                </button>
                <button
                  className={btn}
                  onClick={async () => {
                    if (!window.confirm("Disconnect from Microsoft? The mirrored notes stay in the vault.")) return;
                    await disconnect();
                    await queryClient.invalidateQueries({ queryKey: ["presentations"] });
                  }}
                >
                  Disconnect
                </button>
              </>
            ) : (
              <button className={btn} onClick={() => connect.mutate()} disabled={connect.isPending || code !== null}>
                Connect Microsoft account
              </button>
            )}
          </div>
          {message && <p className="text-sm text-slate-600 dark:text-slate-300">{message}</p>}
          {connect.error && <p className="text-sm text-red-600">{connect.error.message}</p>}
          {sync.data && <p className="text-sm text-slate-600 dark:text-slate-300">Synced: {summarize(sync.data)}</p>}
          {sync.error && <p className="text-sm text-red-600">{sync.error.message}</p>}
          {sync.data?.errors.map((e) => (
            <p key={e} className="text-sm text-red-600">
              {e}
            </p>
          ))}
        </>
      )}
    </section>
  );
}
