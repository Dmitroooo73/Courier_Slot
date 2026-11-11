import { useEffect, useState } from "react";
import type { Profile } from "../api/auth";
import type { AuthState } from "../useAuth";

export function Avatar({ profile, request }: { profile: Profile; request: AuthState["request"] }) {
  const [loaded, setLoaded] = useState<{ id: string; url: string }>();
  const { avatar_id: avatarId, account } = profile;
  const name = profile.display_name || account.login;

  useEffect(() => {
    if (!avatarId) return;
    const controller = new AbortController();
    let objectUrl: string | undefined;
    void request(`/auth/avatars/${account.id}`, { signal: controller.signal })
      .then((response) => response.blob())
      .then((blob) => {
        if (controller.signal.aborted) return;
        objectUrl = URL.createObjectURL(blob);
        setLoaded({ id: avatarId, url: objectUrl });
      }).catch(() => {});
    return () => {
      controller.abort();
      if (objectUrl) URL.revokeObjectURL(objectUrl);
    };
  }, [account.id, avatarId, request]);

  return loaded && avatarId === loaded.id ? <img className="profile-avatar" src={loaded.url} alt="Аватар" />
    : <span className="profile-avatar avatar-placeholder" aria-hidden="true">{Array.from(name).slice(0, 2).join("").toUpperCase()}</span>;
}
