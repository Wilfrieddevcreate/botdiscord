"""
Scraping de disboard.org avec nodriver + clic automatique sur la case Cloudflare.
Necessite : pip install nodriver  (et Google Chrome installe)

Usage : python disboard_scrape_nodriver.py [url] [--no-visit] [--accept] [--max=N] [--login]

--accept : clique "Accepter l'invitation" (compte Discord connecte dans la fenetre ;
           pauses de 20-40 s, plafond --max, defaut 10). Captcha/login : a faire a la main.
           Peut contrevenir aux conditions de Discord : a vos risques.

- Les challenges "Un instant..." sont clics automatiquement. Si Cloudflare
  refuse (detection), le script attend que vous cochiez la case a la main.
- Les liens "Rejoindre" sont navigues dans le meme onglet (pas de nouvel onglet).
Utilisez-le uniquement dans le respect des conditions du site.
"""

import asyncio
import json
import os
import random
import re
import sys

# Chrome plante sous Wayland (plugin Qt introuvable) : on force X11
os.environ.pop("WAYLAND_DISPLAY", None)
os.environ["XDG_SESSION_TYPE"] = "x11"
os.environ["QT_QPA_PLATFORM"] = "xcb"

import shutil  # noqa: E402

import nodriver as uc  # noqa: E402

URL = "https://disboard.org/fr/servers/tag/nsfw"
PROFILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".browser_profile_nd")


async def start_browser(profile_dir):
    """Lance Chrome (CHROME_PATH, sinon google-chrome / chromium du PATH)."""
    chrome = (os.environ.get("CHROME_PATH") or shutil.which("google-chrome")
              or shutil.which("chromium") or shutil.which("chrome"))
    if not chrome:
        raise SystemExit("Chrome introuvable : definissez CHROME_PATH.")
    return await uc.start(
        browser_executable_path=chrome,
        headless=False,
        user_data_dir=profile_dir,
        browser_args=["--disable-dev-shm-usage", "--mute-audio"],
        sandbox=not (hasattr(os, "geteuid") and os.geteuid() == 0),
    )

# Expose les shadow roots fermes de Cloudflare (execute avant chaque page)
HOOK = """
(() => {
  window._roots = window._roots || [];
  const orig = Element.prototype.attachShadow;
  Element.prototype.attachShadow = function (init) {
    const root = orig.call(this, { ...init, mode: 'open' });
    window._roots.push(root);
    return root;
  };
})();
"""

IS_CHALLENGE = "/just a moment|un instant|attention required|verification/i.test(document.title)"


async def js(tab, code):
    raw = await tab.evaluate(f"JSON.stringify(({code}))")
    if not isinstance(raw, str) or raw == "undefined":
        return None  # erreur JS (page en cours de chargement...)
    return json.loads(raw)


async def cf_iframe_rect(tab):
    return await js(tab, """(() => {
        const frames = [...document.querySelectorAll('iframe')];
        for (const r of (window._roots || [])) frames.push(...r.querySelectorAll('iframe'));
        for (const f of frames) {
            const src = f.src || f.getAttribute('src') || '';
            if (!src.includes('challenges.cloudflare.com')) continue;
            const b = f.getBoundingClientRect();
            if (b.width > 50 && b.height > 20) return {x: b.x, y: b.y, w: b.width, h: b.height};
        }
        return null;
    })()""")


async def pass_challenge(tab, timeout=180):
    """Clique la case Cloudflare ; retombe sur un clic manuel si besoin."""
    loop = asyncio.get_event_loop()
    end = loop.time() + timeout
    clicks, last, warned = 0, 0.0, False
    while loop.time() < end:
        if not await js(tab, IS_CHALLENGE):
            return True
        now = loop.time()
        if clicks < 4 and now - last > 6:
            rect = await cf_iframe_rect(tab)
            if rect:
                cx = rect["x"] + 28 + random.uniform(-3, 3)
                cy = rect["y"] + rect["h"] / 2 + random.uniform(-3, 3)
                print(f"  [cf] clic sur la case ({cx:.0f}, {cy:.0f})")
                await tab.mouse_move(cx - 80, cy - 20)
                await asyncio.sleep(random.uniform(0.15, 0.25))
                await tab.mouse_move(cx, cy)
                await asyncio.sleep(random.uniform(0.08, 0.15))
                await tab.mouse_click(cx, cy)
                clicks += 1
                last = loop.time()
        elif clicks >= 4 and not warned:
            print("  [cf] clic auto sans effet : cochez la case a la main...")
            warned = True
        await asyncio.sleep(1)
    return False


async def goto(tab, url):
    await tab.get(url)
    await asyncio.sleep(1.5)
    if await js(tab, IS_CHALLENGE):
        print("  [cf] challenge detecte")
        if not await pass_challenge(tab):
            raise SystemExit("Cloudflare non valide apres 3 minutes.")
        await asyncio.sleep(1.5)


async def accept_invite(tab, captcha_wait=120):
    """Clique "Accepter l'invitation" puis lit le resultat. Ne bloque jamais :
    erreur Discord, bouton absent ou sans effet -> on renvoie un motif et on passe.
    Captcha : attend que vous le resolviez (captcha_wait s max)."""
    loop = asyncio.get_event_loop()
    t0 = loop.time()
    clicked_at = None
    captcha_since = None
    while loop.time() - t0 < 60 + captcha_wait:
        state = await js(tab, """(() => {
            if (location.pathname.startsWith('/channels/')) return ['joined', ''];
            if (document.querySelector('iframe[src*="captcha"]')) return ['captcha', ''];
            const err = (document.body ? document.body.innerText : '').match(
                /ne peux pas[^\\n]*|invitation (invalide|expir)[^\\n]*|can.t join[^\\n]*|invite[^\\n]*(invalid|expired)[^\\n]*/i);
            if (err) return ['error', err[0].slice(0, 90)];
            return ['none', ''];
        })()""")
        kind, msg = state or ["none", ""]
        if kind == "joined":
            return "rejoint"
        if kind == "error":
            return f"refuse ({msg})"
        if kind == "captcha":
            if captcha_since is None:
                print("    [discord] captcha : resolvez-le a la main (120 s max)...")
                captcha_since = loop.time()
            elif loop.time() - captcha_since > captcha_wait:
                return "captcha non resolu"
        elif clicked_at is None:
            if await js(tab, """(() => {
                const b = [...document.querySelectorAll('button')]
                    .find(x => /accepter l.invitation|accept invite/i.test(x.textContent));
                if (b) { b.click(); return true; }
                return false;
            })()"""):
                clicked_at = loop.time()
            elif loop.time() - t0 > 20:
                return "bouton absent"
        elif loop.time() - clicked_at > 15 and captcha_since is None:
            return "sans effet apres 15 s"
        await asyncio.sleep(1.5)
    return "delai depasse"


async def accept_age(tab):
    for _ in range(6):
        clicked = await js(tab, """(() => {
            const re = /j.ai plus de 18 ans/i;
            const el = [...document.querySelectorAll('a,button,div,span')]
                .find(e => e.children.length === 0 && re.test(e.textContent) && e.offsetParent);
            if (el) { el.click(); return el.textContent.trim(); }
            window.scrollBy(0, 400);
            return null;
        })()""")
        if clicked:
            print("Bouton d'age clique :", clicked)
            return
        await asyncio.sleep(1)
    print("Pas de bouton d'age (deja accepte ?)")


async def login():
    """Ouvre la page de connexion Discord et attend que vous vous connectiez."""
    browser = await start_browser(PROFILE)
    try:
        tab = await browser.get("https://discord.com/login")
        print("Connectez-vous a Discord dans la fenetre (10 min max)...")
        for _ in range(300):
            if re.search(r"discord\.com/(channels|app)", await js(tab, "location.href") or ""):
                print("Connecte. Session enregistree dans .browser_profile_nd/")
                await asyncio.sleep(3)
                return
            await asyncio.sleep(2)
        print("Connexion non detectee.")
    finally:
        browser.stop()


async def main(url, visit, accept=False, max_accept=10):
    browser = await start_browser(PROFILE)
    try:
        tab = await browser.get("about:blank")
        await tab.send(uc.cdp.page.add_script_to_evaluate_on_new_document(source=HOOK))

        await goto(tab, url)
        await accept_age(tab)
        await asyncio.sleep(1.5)

        links = await js(tab, """(() => {
            const seen = new Set(), out = [];
            for (const e of document.querySelectorAll('a')) {
                if (e.textContent.trim() !== 'Rejoindre' || !e.href || seen.has(e.href)) continue;
                seen.add(e.href); out.push(e.href);
            }
            return out;
        })()""") or []
        print(f"{len(links)} liens 'Rejoindre' trouves")
        if not links:
            print("Aucun lien <a> : le bouton n'est peut-etre pas un lien. Titre :",
                  await js(tab, "document.title"))
            return

        done = 0
        for i, link in enumerate(links):
            print(f"[{i}] {link}")
            if not visit:
                continue
            try:
                await goto(tab, link)
                cur = None
                for _ in range(60):
                    cur = await js(tab, "location.href")
                    if re.search(r"discord\.(com|gg)", cur or ""):
                        break
                    if await js(tab, IS_CHALLENGE):
                        print("    [cf] challenge sur la page de redirection")
                        await pass_challenge(tab)
                    await asyncio.sleep(1)
                print(f"    -> {cur}")
                if accept and not re.search(r"discord\.(com|gg)", cur or ""):
                    print("    pas de redirection vers Discord : lien ignore")
                if accept and done < max_accept and re.search(r"discord\.(com|gg)/invite", cur or ""):
                    res = await accept_invite(tab)
                    print(f"    accepter : {res}")
                    done += 1
                    await asyncio.sleep(random.uniform(20, 40) if res == "rejoint" else 3)
                else:
                    await asyncio.sleep(2)
            except Exception as e:  # onglet ferme / connexion coupee
                print(f"    erreur ({type(e).__name__}: {str(e)[:80]}), nouvel onglet...")
                try:
                    tab = await browser.get("about:blank", new_tab=True)
                    await tab.send(uc.cdp.page.add_script_to_evaluate_on_new_document(source=HOOK))
                except Exception:
                    print("Le navigateur a ete ferme, arret.")
                    return
    finally:
        browser.stop()


if __name__ == "__main__":
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    if "--login" in sys.argv:
        asyncio.run(login())
        sys.exit(0)
    n = next((int(a.split("=")[1]) for a in sys.argv if a.startswith("--max=")), 10 )
    asyncio.run(main(args[0] if args else URL, "--no-visit" not in sys.argv,
                     accept="--accept" in sys.argv, max_accept=n))
