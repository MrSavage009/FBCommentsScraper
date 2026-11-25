import os
import re
import json
import time
import shutil
import subprocess
from pathlib import Path
from datetime import datetime
from playwright.sync_api import sync_playwright

try:
    import google.generativeai as genai
except ImportError:
    genai = None

# Enable ANSI escape codes in Windows terminals
os.system("")

# ==========================================
# 🔹 MASTER CONFIGURATION
# ==========================================
API_KEY = "api"
MODEL_ID = "gemini-2.5-flash"

LOCAL_DIR = Path.cwd()  # Folder where Python was opened (or use Path(__file__).resolve().parent)

BASE_DIR = Path(
    r"C:\Users\XXX\Documents\FBscraper"
)
SESSION_PATH = LOCAL_DIR / "browser_state.json"
REAL_PROFILE_DIR = LOCAL_DIR / "chrome_real_profile"
FB_ARCHIVE_ROOT = LOCAL_DIR / "Facebook Comment Archives"
FB_TXT_DIR = FB_ARCHIVE_ROOT / "TXT"
FB_JSON_DIR = FB_ARCHIVE_ROOT / "JSON"

for p in [SESSION_PATH.parent, REAL_PROFILE_DIR, FB_TXT_DIR, FB_JSON_DIR]:
    p.mkdir(parents=True, exist_ok=True)

# ==========================================
# ⚡ RUNTIME CONFIGURATION (Editable via Menu)
# ==========================================
CONFIG = {
    # 1 = Conservative (huge threads), 2 = Fast (balanced), 3 = Aggressive, 4 = Custom
    "speed_profile": 2,
    "custom_step_px": 550,
    "custom_wait_ms": 500,
    "custom_stable_thresh": 4,
    # Automatically switch sort dropdown to "Alle opmerkingen" / "All comments"
    "force_all_comments": True,
    # Max click retries on a single expand button instance if DOM doesn't change
    "max_button_retries": 3,
    # Max expansion waves per scroll viewport before advancing scroll
    "max_waves_per_view": 40,
    # Title resolution: 1 = Page/Author + Snippet, 2 = Gemini API Summary -> Fallback
    "title_mode": 1,
    # Save JSON alongside TXT
    "save_json": True,
}

# ==========================================
# 🎨 TERMINAL UI HELPERS
# ==========================================
CYAN = "\033[96m"
GREEN = "\033[92m"
YELLOW = "\033[93m"
RED = "\033[91m"
MAGENTA = "\033[95m"
DIM = "\033[90m"
BOLD = "\033[1m"
RESET = "\033[0m"


def normalize_url(raw_url, default_url=None):
    url = (raw_url or "").strip().strip('"').strip("'")
    if not url:
        return default_url or ""
    if not re.match(r"^https?://", url, re.IGNORECASE):
        url = "https://" + url.lstrip("/")
    return url


def get_session_status():
    if not SESSION_PATH.exists():
        return f"{RED}Missing{RESET} (Run Auth first)"
    stat = SESSION_PATH.stat()
    mtime = datetime.fromtimestamp(stat.st_mtime).strftime("%Y-%m-%d %H:%M")
    origins = 0
    fb_present = False
    try:
        data = json.loads(SESSION_PATH.read_text(encoding="utf-8"))
        cookies = data.get("cookies", [])
        origins = len(data.get("origins", []))
        fb_present = any("facebook.com" in c.get("domain", "") for c in cookies)
    except Exception:
        pass
    fb_badge = f"{GREEN}FB Cookie OK{RESET}" if fb_present else f"{YELLOW}No FB Cookie{RESET}"
    return f"{GREEN}Active{RESET} ({fb_badge} | {origins} origins | {mtime})"


def print_banner():
    speed_names = {1: "Conservative", 2: "Fast", 3: "Aggressive", 4: "Custom"}
    title_names = {1: "Post Author & Snippet", 2: "Gemini AI Summary"}
    sp = CONFIG["speed_profile"]
    tm = CONFIG["title_mode"]

    print(f"\n{CYAN}{BOLD}╔══════════════════════════════════════════════════════════════╗")
    print(f"║        🚀 FACEBOOK COMMENT ARCHIVER & AUTH SUITE v1.3        ║")
    print(f"╚══════════════════════════════════════════════════════════════╝{RESET}")
    print(f"  {BOLD}Session:{RESET}       {get_session_status()}")
    print(f"  {BOLD}Speed Profile:{RESET} {CYAN}{sp} ({speed_names.get(sp, 'Custom')}){RESET}  |  "
          f"{BOLD}Sort:{RESET} {'Alle opmerkingen' if CONFIG['force_all_comments'] else 'Default'}")
    print(f"  {BOLD}Title Mode:{RESET}    {title_names.get(tm, 'Default')}  |  "
          f"{BOLD}JSON Export:{RESET} {'ON' if CONFIG['save_json'] else 'OFF'}")
    print(f"  {BOLD}Output Dir:{RESET}    {DIM}{FB_ARCHIVE_ROOT}{RESET}")
    print(f"{DIM}{'─' * 64}{RESET}")


def clip(text, max_len=120):
    if not text:
        return ""
    flat = " ".join(text.replace("\r\n", "\n").split())
    return flat if len(flat) <= max_len else flat[: max_len - 3].rstrip() + "..."


def safe_filename(text, max_len=80):
    cleaned = re.sub(r'[\\/*?:"<>|\r\n\t]+', " ", text or "")
    cleaned = " ".join(cleaned.split()).strip(" .")
    if len(cleaned) > max_len:
        cleaned = cleaned[:max_len].rstrip()
    return cleaned or "Facebook Post"


# ==========================================
# 🔐 CUMULATIVE BROWSER CONTEXT & CDP MERGE
# ==========================================
def save_cumulative_state(new_state: dict):
    """Merge newly captured cookies & origins into browser_state.json without losing prior sessions."""
    merged_cookies = {}
    merged_origins = {}

    if SESSION_PATH.exists():
        try:
            old = json.loads(SESSION_PATH.read_text(encoding="utf-8"))
            for c in old.get("cookies", []):
                key = (c.get("name"), c.get("domain"), c.get("path"))
                merged_cookies[key] = c
            for o in old.get("origins", []):
                merged_origins[o.get("origin")] = o
        except Exception:
            pass

    for c in new_state.get("cookies", []):
        key = (c.get("name"), c.get("domain"), c.get("path"))
        merged_cookies[key] = c
    for o in new_state.get("origins", []):
        merged_origins[o.get("origin")] = o

    final_state = {
        "cookies": list(merged_cookies.values()),
        "origins": list(merged_origins.values()),
    }
    SESSION_PATH.write_text(json.dumps(final_state, indent=2), encoding="utf-8")


def launch_browser(p):
    """Launch real Chrome or Edge cleanly (avoids Arkose Labs bundled-Chromium/automation flags)."""
    common_args = [
        "--disable-blink-features=AutomationControlled",
        "--start-maximized",
    ]
    for channel in ("chrome", "msedge"):
        try:
            browser = p.chromium.launch(
                channel=channel,
                headless=False,
                ignore_default_args=["--enable-automation"],
                args=common_args,
            )
            print(f"   ✅ Launched genuine browser channel: {BOLD}{channel}{RESET}")
            return browser
        except Exception:
            continue

    print(f"   {YELLOW}⚠️ Chrome/Edge channel unavailable; falling back to bundled Chromium.{RESET}")
    return p.chromium.launch(
        headless=False,
        ignore_default_args=["--enable-automation"],
        args=common_args,
    )


def create_stealth_context(browser):
    """Create context with matching native UA/ClientHints and existing cumulative state."""
    ctx_kwargs = {"no_viewport": True}
    if SESSION_PATH.exists():
        ctx_kwargs["storage_state"] = str(SESSION_PATH)
    return browser.new_context(**ctx_kwargs)


def find_desktop_browser_exe():
    candidates = [
        Path(os.environ.get("PROGRAMFILES", "")) / "Google/Chrome/Application/chrome.exe",
        Path(os.environ.get("PROGRAMFILES(X86)", "")) / "Google/Chrome/Application/chrome.exe",
        Path(os.environ.get("LOCALAPPDATA", "")) / "Google/Chrome/Application/chrome.exe",
        Path(os.environ.get("PROGRAMFILES(X86)", "")) / "Microsoft/Edge/Application/msedge.exe",
        Path(os.environ.get("PROGRAMFILES", "")) / "Microsoft/Edge/Application/msedge.exe",
    ]
    for c in candidates:
        if c.exists():
            return str(c)
    for name in ("chrome", "msedge"):
        found = shutil.which(name)
        if found:
            return found
    return None


# ==========================================
# 🔑 AUTHENTICATION MODULE (Integrated auth.py)
# ==========================================
def run_auth_flow(default_url="https://www.facebook.com/"):
    print(f"\n{MAGENTA}{BOLD}🔐 CUMULATIVE SESSION AUTHENTICATOR{RESET}")
    print(f"{DIM}Preserves existing cookies/origins in browser_state.json and merges new logins.{RESET}\n")
    print(f"  {BOLD}[A]{RESET} Clean Playwright Chrome/Edge Launch (Recommended)")
    print(f"  {BOLD}[B]{RESET} Real Standalone Desktop Chrome via CDP (Use if Arkose MatchKey blocks [A])")

    mode = (input("\nSelect auth mode [A/B] (Enter for A): ").strip() or "A").upper()
    raw_url = input(f"🔗 Target URL (Enter for {default_url}): ")
    target_url = normalize_url(raw_url, default_url=default_url)

    if mode == "B":
        exe = find_desktop_browser_exe()
        if not exe:
            print(f"{RED}❌ Could not find chrome.exe or msedge.exe on this PC.{RESET}")
            return
        print(f"\n🖥️  Launching standalone desktop browser: {DIM}{exe}{RESET}")
        proc = subprocess.Popen(
            [
                exe,
                f"--user-data-dir={REAL_PROFILE_DIR}",
                "--remote-debugging-port=9222",
                "--no-first-run",
                "--no-default-browser-check",
                target_url,
            ],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        print(f"\n{YELLOW}{BOLD}👉 ACTION REQUIRED (Standalone Browser):{RESET}")
        print("  1. Complete login, Arkose MatchKey, and 2FA in the real Chrome/Edge window.")
        print("  2. Once Facebook is fully logged in and loading posts normally, return here.")
        input(f"\n{BOLD}Press [Enter] here to sync cookies into browser_state.json...{RESET}")

        try:
            with sync_playwright() as p:
                browser = p.chromium.connect_over_cdp("http://127.0.0.1:9222")
                if browser.contexts:
                    new_state = browser.contexts[0].storage_state()
                    save_cumulative_state(new_state)
                    print(f"\n{GREEN}✅ Standalone session merged cumulatively into:{RESET} {SESSION_PATH}")
                else:
                    print(f"{RED}❌ No browser context found on CDP port 9222.{RESET}")
                try:
                    browser.close()
                except Exception:
                    pass
        except Exception as e:
            print(f"{RED}❌ CDP sync error:{RESET} {e}")
        finally:
            try:
                proc.terminate()
            except Exception:
                pass
        return

    with sync_playwright() as p:
        print("🖥️  Launching clean Chrome/Edge...")
        browser = launch_browser(p)
        if SESSION_PATH.exists():
            print("📂 Found existing session state. Loading prior logins...")
        else:
            print("🆕 No existing session state found. Creating fresh context...")

        context = create_stealth_context(browser)
        page = context.new_page()

        try:
            print(f"🌐 Navigating to {target_url}...")
            page.goto(target_url, wait_until="domcontentloaded", timeout=120000)

            print(f"\n{YELLOW}{BOLD}👉 ACTION REQUIRED:{RESET}")
            print("  1. Log into your account manually in the opened window.")
            print("  2. Accept/dismiss any cookie consent banners and complete 2FA / MatchKey.")
            print("  3. Once the dashboard/feed is fully loaded, return here.")

            input(f"\n{BOLD}Press [Enter] here ONLY after login is complete...{RESET}")

            save_cumulative_state(context.storage_state())
            print(f"\n{GREEN}✅ Cumulative session saved to:{RESET} {SESSION_PATH}")
        except Exception as e:
            print(f"{RED}❌ Auth error:{RESET} {e}")
        finally:
            try:
                browser.close()
            except Exception:
                pass


# ==========================================
# 🧠 OPTIONAL GEMINI TITLE GENERATOR
# ==========================================
def resolve_title(post_data, comments_list):
    author = post_data.get("author") or "Facebook"
    post_text = post_data.get("text") or ""

    if CONFIG["title_mode"] == 2 and genai is not None and API_KEY:
        try:
            genai.configure(api_key=API_KEY)
            model = genai.GenerativeModel(MODEL_ID)
            sample = f"Post by {author}:\n{post_text[:2000]}\n\nTop comments:\n"
            for c in comments_list[:15]:
                sample += f"- {c.get('author')}: {c.get('text', '')[:200]}\n"
            prompt = (
                "Return a concise 5 to 7 word title summarizing this Facebook post and discussion. "
                "Plain text only, no quotes or markdown:\n\n" + sample
            )
            resp = model.generate_content(prompt)
            title = resp.text.strip().replace('"', "").replace("*", "")
            if title:
                return f"{author} - {title}", "Gemini API"
        except Exception as e:
            print(f"   {YELLOW}⚠️ Gemini title skipped ({e}), using Post Snippet.{RESET}")

    snippet = clip(post_text, 48)
    if snippet:
        return f"{author} - {snippet}", "Post Snippet"
    page_title = (post_data.get("pageTitle") or "").replace("| Facebook", "").strip()
    if page_title:
        return page_title, "Page Title"
    return f"{author} Post", "Fallback"


# ==========================================
# 🕷️ FACEBOOK COMMENT SCRAPER ENGINE
# ==========================================
DOM_ENGINE_JS = r"""
() => {
    if (window.__fbEngineInstalled) return;
    window.__fbEngineInstalled = true;

    window.__fbComments = {};       // id -> comment object
    window.__fbOrder = [];          // ordered array of ids
    window.__fbDirtyIds = new Set();// ids added/updated since last Python drain
    window.__fbDeadButtons = {};    // uniqueButtonKey -> true
    window.__fbBtnAttempts = {};    // uniqueButtonKey -> attempt count
    window.__fbBtnSeq = 0;          // monotonic DOM element tagger
    window.__fbMutationTick = 0;    // incremented on every DOM mutation

    // 1. Scope detection: prefer modal dialog if it holds the post/comments, else main
    window.__fbGetScopeRoot = function() {
        const dialogs = Array.from(document.querySelectorAll('div[role="dialog"][aria-modal="true"]'));
        for (const d of dialogs) {
            if (d.querySelector('[role="article"]') || d.querySelector('[data-ad-rendering-role="story_message"]')) {
                return d;
            }
        }
        return document.querySelector('div[role="main"]') || document.body;
    };

    // 2. Find the actual scrollable container inside the active scope
    window.__fbGetScrollContainer = function() {
        const root = window.__fbGetScopeRoot();
        const isDialog = root.getAttribute('role') === 'dialog';
        if (isDialog) {
            const candidates = Array.from(root.querySelectorAll('div')).filter(el => {
                const st = window.getComputedStyle(el);
                const oy = st.overflowY;
                return (oy === 'auto' || oy === 'scroll') && el.scrollHeight > el.clientHeight + 40;
            });
            if (candidates.length > 0) {
                candidates.sort((a, b) => b.clientHeight - a.clientHeight);
                return candidates[0];
            }
        }
        return document.scrollingElement || document.documentElement;
    };

    // 3. Emoji-preserving & mention-preserving text extractor
    window.__fbExtractText = function(el) {
        if (!el) return '';
        const out = [];
        function walk(node) {
            if (node.nodeType === Node.TEXT_NODE) {
                out.push(node.textContent);
                return;
            }
            if (node.nodeType !== Node.ELEMENT_NODE) return;
            const tag = node.tagName.toUpperCase();
            if (tag === 'SCRIPT' || tag === 'STYLE' || tag === 'SVG') return;
            if (tag === 'BR') { out.push('\n'); return; }
            if (tag === 'IMG') {
                const alt = node.getAttribute('alt');
                if (alt) out.push(alt);
                return;
            }
            if (node.getAttribute('role') === 'button') {
                const bt = (node.innerText || '').trim().toLowerCase();
                if (bt === 'meer weergeven' || bt === 'see more') return;
            }
            const isBlock = (tag === 'DIV' || tag === 'P') && node.getAttribute('dir') === 'auto';
            for (const child of node.childNodes) walk(child);
            if (isBlock) out.push('\n');
        }
        walk(el);
        return out.join('')
            .replace(/\u00a0/g, ' ')
            .replace(/[ \t]+\n/g, '\n')
            .replace(/\n{3,}/g, '\n\n')
            .trim();
    };

    // 4. Extract Post Header & Content
    window.__fbExtractPost = function() {
        const root = window.__fbGetScopeRoot();
        let author = '';
        const nameEl = root.querySelector('[data-ad-rendering-role="profile_name"]');
        if (nameEl) {
            author = (nameEl.innerText || '').replace(/\s*·\s*Volgen\s*$/i, '').replace(/\s*·\s*Follow\s*$/i, '').trim();
        }
        if (!author) {
            const h2 = root.querySelector('h2');
            if (h2) {
                author = (h2.innerText || '').replace(/^(Bericht van|Post by)\s+/i, '').trim();
            }
        }

        let timestampFull = '';
        let timestampShort = '';
        const timeLinks = Array.from(root.querySelectorAll('a[aria-describedby], a[aria-labelledby]'));
        for (const a of timeLinks) {
            if (a.closest('[role="article"]')) continue;
            const descId = a.getAttribute('aria-describedby');
            const lblId = a.getAttribute('aria-labelledby');
            if (descId) {
                const descEl = document.getElementById(descId);
                if (descEl && descEl.innerText) timestampFull = descEl.innerText.trim();
            }
            if (lblId) {
                const lblEl = document.getElementById(lblId);
                if (lblEl && lblEl.innerText) timestampShort = lblEl.innerText.trim();
            }
            if (timestampFull || timestampShort) break;
        }

        let text = '';
        const msgEl = root.querySelector('[data-ad-rendering-role="story_message"]');
        if (msgEl) {
            text = window.__fbExtractText(msgEl);
        }

        let advertisedComments = null;
        const cmtBtn = root.querySelector('[data-ad-rendering-role="comment_button"]');
        if (cmtBtn) {
            const btn = cmtBtn.closest('[role="button"]');
            if (btn) {
                const aria = btn.getAttribute('aria-label') || '';
                const m = aria.match(/([\d.,]+)\s+(opmerking|comment)/i);
                if (m) advertisedComments = m[1];
                else {
                    const txt = (btn.innerText || '').trim();
                    if (txt) advertisedComments = txt;
                }
            }
        }

        return {
            pageTitle: document.title || '',
            author,
            timestampFull,
            timestampShort,
            text,
            advertisedComments
        };
    };

    // 5. Parse URL params & decode base64 Facebook comment_id ("comment:<postId>_<commentId>")
    window.__fbDecodeCommentParam = function(val) {
        if (!val) return '';
        if (/^\d+$/.test(val)) return val;
        try {
            const decoded = atob(val);
            const m = decoded.match(/_(\d+)$/);
            if (m) return m[1];
        } catch (e) {}
        return val;
    };

    window.__fbParseIds = function(href) {
        let commentId = '';
        let replyId = '';
        if (!href) return { commentId, replyId };
        try {
            const u = new URL(href, window.location.origin);
            commentId = window.__fbDecodeCommentParam(u.searchParams.get('comment_id') || '');
            replyId = window.__fbDecodeCommentParam(u.searchParams.get('reply_comment_id') || '');
        } catch (e) {}
        return { commentId, replyId };
    };

    // 6. Extract a single real role="article" comment or reply
    window.__fbExtractOneArticle = function(art) {
        if (!art) return null;
        if (art.querySelector('[role="status"][aria-label*="Laden"], [role="status"][aria-label*="Loading"]')) {
            return null;
        }
        const ariaLabel = art.getAttribute('aria-label') || '';
        if (!ariaLabel) return null;

        const isReply = /^(Antwoord van|Reply by)\b/i.test(ariaLabel);
        const isNestedReply = /\b(op het antwoord van|to .+'s reply)\b/i.test(ariaLabel);

        let author = '';
        let authorUrl = '';
        let timestampFull = '';
        let timestampShort = '';
        let tsCommentId = '';
        let tsReplyId = '';
        let fallbackCommentId = '';
        let permalink = '';

        const links = Array.from(art.querySelectorAll('a[href]'));
        for (const a of links) {
            const href = a.getAttribute('href') || '';
            const aAria = a.getAttribute('aria-label') || '';
            if (!href.includes('comment_id=')) continue;

            const ids = window.__fbParseIds(href);

            const isTimestampLink = (
                ids.replyId ||
                (aAria && /\d{4}|\d{1,2}:\d{2}|\d+\s*(uur|dag|min|sec|week|maand|jaar|hour|day|min|sec|week|month|year)/i.test(aAria))
            );

            if (isTimestampLink) {
                if (ids.commentId) tsCommentId = ids.commentId;
                if (ids.replyId) tsReplyId = ids.replyId;
                if (aAria) timestampFull = aAria.trim();
                timestampShort = (a.innerText || '').trim();
                permalink = href;
            } else {
                if (ids.commentId && !fallbackCommentId) fallbackCommentId = ids.commentId;
                if (!author && (a.innerText || '').trim() && a.getAttribute('aria-hidden') !== 'true') {
                    author = (a.innerText || '').trim();
                    authorUrl = href.split('?')[0];
                }
            }
        }

        if (!author) {
            const m = ariaLabel.match(/^(?:Opmerking van|Comment by|Antwoord van|Reply by)\s+(.+?)\s+(?:\d+|een|a|gisteren|yesterday|vandaag|today|op\s+de|to\s+)/i);
            if (m) author = m[1].trim();
        }

        let text = '';
        const textContainer = art.querySelector('.x1lliihq.xjkvuk6.x1iorvi4') || art.querySelector('span[dir="auto"][lang]');
        if (textContainer) {
            text = window.__fbExtractText(textContainer);
        }

        const attachments = [];
        const mediaImgs = Array.from(art.querySelectorAll('a[href*="photo.php"], a[href*="/photo/"] img, img.xz74otr'));
        for (const img of mediaImgs) {
            const src = img.getAttribute('src') || '';
            if (src.includes('emoji.php')) continue;
            if (img.closest('[role="button"]') && !img.closest('a[href*="photo"]')) continue;
            const alt = (img.getAttribute('alt') || 'Image').trim();
            if (src) attachments.push({ alt, src });
        }

        let reactions = 0;
        let reactionsLabel = '';
        const reactBtn = art.querySelector('[role="button"][aria-label*="reactie"], [role="button"][aria-label*="reaction"]');
        if (reactBtn) {
            reactionsLabel = reactBtn.getAttribute('aria-label') || '';
            const rm = reactionsLabel.match(/^([\d.,]+)/);
            if (rm) {
                reactions = parseInt(rm[1].replace(/[.,]/g, ''), 10) || 0;
            }
        }

        const isEdited = Array.from(art.querySelectorAll('[role="button"]')).some(b => {
            const t = (b.innerText || '').trim().toLowerCase();
            return t === 'bewerkt' || t === 'edited';
        });

        let id = isReply ? (tsReplyId || fallbackCommentId) : (tsCommentId || fallbackCommentId);
        if (!id) {
            id = 'hash_' + btoa(unescape(encodeURIComponent((ariaLabel + '|' + text.slice(0, 60)).slice(0, 100)))).replace(/[^a-zA-Z0-9]/g, '').slice(0, 24);
        }

        let parentId = null;
        if (isReply) {
            parentId = tsCommentId || null;
            if (!parentId || parentId === id) {
                const threadBox = art.closest('.x18xomjl');
                if (threadBox) {
                    const topArt = threadBox.querySelector('[role="article"][aria-label^="Opmerking van"], [role="article"][aria-label^="Comment by"]');
                    if (topArt && topArt !== art) {
                        const pLinks = Array.from(topArt.querySelectorAll('a[href*="comment_id="]'));
                        for (const pl of pLinks) {
                            const pids = window.__fbParseIds(pl.getAttribute('href'));
                            if (pids.commentId && !pids.replyId) {
                                parentId = pids.commentId;
                                break;
                            }
                        }
                    }
                }
            }
        }

        const depth = isNestedReply ? 2 : (isReply ? 1 : 0);

        return {
            id,
            parentId,
            depth,
            isReply,
            author,
            authorUrl,
            timestampFull,
            timestampShort,
            text,
            attachments,
            reactions,
            reactionsLabel,
            isEdited,
            ariaLabel,
            permalink
        };
    };

    // 7. Harvest all visible articles & upsert into __fbComments
    window.__fbHarvest = function() {
        const root = window.__fbGetScopeRoot();
        const articles = Array.from(root.querySelectorAll('[role="article"]'));
        let added = 0;
        let updated = 0;

        for (const art of articles) {
            const data = window.__fbExtractOneArticle(art);
            if (!data) continue;
            const existing = window.__fbComments[data.id];
            if (!existing) {
                window.__fbComments[data.id] = data;
                window.__fbOrder.push(data.id);
                window.__fbDirtyIds.add(data.id);
                added++;
            } else {
                let changed = false;
                if (data.text && data.text.length > (existing.text || '').length) {
                    existing.text = data.text;
                    changed = true;
                }
                if (data.reactions > existing.reactions) {
                    existing.reactions = data.reactions;
                    existing.reactionsLabel = data.reactionsLabel;
                    changed = true;
                }
                if ((!existing.parentId || existing.parentId === existing.id) && data.parentId && data.parentId !== existing.id) {
                    existing.parentId = data.parentId;
                    changed = true;
                }
                if (changed) {
                    window.__fbDirtyIds.add(data.id);
                    updated++;
                }
            }
        }
        return {
            added,
            updated,
            totalCaptured: window.__fbOrder.length,
            domArticles: articles.length
        };
    };

    // 7b. Drain delta updates for live Python-side sync (crash/close-safe!)
    window.__fbDrainUpdates = function() {
        const updates = Array.from(window.__fbDirtyIds)
            .map(id => window.__fbComments[id])
            .filter(Boolean);
        window.__fbDirtyIds.clear();
        return {
            order: window.__fbOrder.slice(),
            updates
        };
    };

    // 8. Click "Meer weergeven" / "See more" inside post & visible comments ONLY
    window.__fbExpandSeeMore = function() {
        const root = window.__fbGetScopeRoot();
        let clicked = 0;
        const buttons = Array.from(root.querySelectorAll('[role="button"]'));
        for (const b of buttons) {
            const txt = (b.innerText || '').trim().toLowerCase();
            if (txt !== 'meer weergeven' && txt !== 'see more') continue;
            if (!b.closest('[data-ad-rendering-role="story_message"]') && !b.closest('[role="article"]')) continue;
            if (b.dataset.fbSeenClicked === '1') continue;
            b.dataset.fbSeenClicked = '1';
            try {
                b.scrollIntoView({ block: 'center', behavior: 'instant' });
                b.click();
                clicked++;
            } catch (e) {}
        }
        return clicked;
    };

    // 9. Find safe expansion buttons (Load more comments / View replies / More replies)
    window.__fbFindExpandButtons = function() {
        const root = window.__fbGetScopeRoot();
        const btns = Array.from(root.querySelectorAll('[role="button"]'));
        const candidates = [];

        const excludeAriaRe = /(samenvouwen|collapse|verbergen|hide|rapporteren|report|omlaag stemmen|downvote|beantwoorden|reply|bewerkt|edited|delen|share|sluiten|close|acties|actions)/i;
        const includeTextRe = new RegExp([
            'meer opmerkingen',
            'vorige opmerkingen',
            'alle .*opmerkingen',
            'view more comments',
            'view previous comments',
            'more comments',
            'show more comments',
            'load more comments',
            '\\d+\\s+antwoord(en)?\\s+(bekijken|weergeven|tonen)',
            'alle\\s+\\d+\\s+antwoord',
            'bekijk.*antwoord',
            'meer antwoorden',
            'vorige antwoorden',
            'nog\\s+\\d+\\s+antwoord',
            'view.*\\d+\\s+repl(y|ies)',
            'view more replies',
            'view previous replies',
            'show more replies',
            'more replies',
            '^\\d+\\s+repl(y|ies)$'
        ].join('|'), 'i');

        for (const b of btns) {
            if (b.getAttribute('aria-expanded') === 'true') continue;
            if (b.getAttribute('aria-haspopup')) continue;
            const aria = b.getAttribute('aria-label') || '';
            if (excludeAriaRe.test(aria)) continue;

            const txt = (b.innerText || '').replace(/\s+/g, ' ').trim();
            if (!txt || txt.length > 80) continue;
            if (!includeTextRe.test(txt)) continue;

            const rect = b.getBoundingClientRect();
            if (rect.width === 0 && rect.height === 0) continue;

            if (!b.dataset.fbBtnId) {
                window.__fbBtnSeq += 1;
                b.dataset.fbBtnId = 'fb_btn_' + window.__fbBtnSeq;
            }
            const key = b.dataset.fbBtnId + '::' + txt.toLowerCase();
            if (window.__fbDeadButtons[key]) continue;

            candidates.push({
                btnId: b.dataset.fbBtnId,
                key,
                txt,
                top: rect.top
            });
        }
        return candidates;
    };

    // 10. Status check: are loading spinners visible inside scope?
    window.__fbHasActiveSpinner = function() {
        const root = window.__fbGetScopeRoot();
        const spinners = Array.from(root.querySelectorAll('[role="status"][aria-label*="Laden"], [role="status"][aria-label*="Loading"]'));
        return spinners.some(s => {
            const rect = s.getBoundingClientRect();
            return rect.width > 0 && rect.height > 0 && rect.top < window.innerHeight + 300;
        });
    };

    // 11. Install MutationObserver on scope root
    const target = window.__fbGetScopeRoot();
    window.__fbObserver = new MutationObserver(() => {
        window.__fbMutationTick += 1;
    });
    window.__fbObserver.observe(target, { childList: true, subtree: true });
}
"""


def sync_live_state(page, live_state):
    """Pull delta updates from browser JS into Python memory so closing/interrupting loses nothing."""
    delta = page.evaluate("() => window.__fbDrainUpdates ? window.__fbDrainUpdates() : null")
    if not delta:
        return
    if delta.get("order"):
        live_state["order"] = delta["order"]
    for item in delta.get("updates", []):
        cid = item.get("id")
        if cid:
            live_state["comments_by_id"][cid] = item


def wait_for_spinners_and_mutations(page, prev_tick, timeout_s=4.5):
    """Wait until loading spinners clear AND DOM mutations settle after a click."""
    deadline = time.time() + timeout_s
    start_wait_until = min(deadline, time.time() + 1.2)
    while time.time() < start_wait_until:
        cur_tick = page.evaluate("() => window.__fbMutationTick || 0")
        spinner = page.evaluate("() => window.__fbHasActiveSpinner ? window.__fbHasActiveSpinner() : false")
        if cur_tick > prev_tick or spinner:
            break
        page.wait_for_timeout(120)

    last_tick = page.evaluate("() => window.__fbMutationTick || 0")
    quiet_since = time.time()
    while time.time() < deadline:
        spinner = page.evaluate("() => window.__fbHasActiveSpinner ? window.__fbHasActiveSpinner() : false")
        cur_tick = page.evaluate("() => window.__fbMutationTick || 0")
        if cur_tick != last_tick:
            last_tick = cur_tick
            quiet_since = time.time()
        elif not spinner and (time.time() - quiet_since) >= 0.35:
            return
        page.wait_for_timeout(150)


def handle_login_or_consent_if_needed(page, context):
    """Detect Facebook login wall and let user resolve in-flight if needed."""
    time.sleep(2.5)
    needs_login = page.evaluate(r"""() => {
        const url = window.location.href.toLowerCase();
        if (url.includes('/login') || url.includes('checkpoint')) return true;
        const emailInput = document.querySelector('input[name="email"]');
        const passInput = document.querySelector('input[name="pass"]');
        if (emailInput && passInput && emailInput.offsetParent !== null) return true;
        return false;
    }""")

    if needs_login:
        print(f"\n{YELLOW}{BOLD}🔐 Facebook Login / Verification Detected!{RESET}")
        print("  1. Please complete the login / 2FA in the opened Chromium window.")
        print("  2. Make sure the target post & comments are visible.")
        input(f"  {BOLD}Press [Enter] once logged in to save session and resume scraping...{RESET}")
        save_cumulative_state(context.storage_state())
        print(f"  {GREEN}✅ Session updated & saved to {SESSION_PATH.name}{RESET}")
        time.sleep(2)


def switch_comment_sort_to_all(page):
    """Locate the comment filter dropdown and switch to 'Alle opmerkingen' / 'All comments'."""
    if not CONFIG["force_all_comments"]:
        return

    sort_state = page.evaluate(r"""() => {
        const root = window.__fbGetScopeRoot();
        const sortRe = /^(meest relevant|relevantste|populairste|nieuwste|alle opmerkingen|most relevant|top comments|newest|all comments)$/i;
        const btns = Array.from(root.querySelectorAll('[role="button"][aria-haspopup="menu"]'));
        for (const b of btns) {
            const txt = (b.innerText || '').replace(/\s+/g, ' ').trim();
            if (sortRe.test(txt)) {
                return { found: true, current: txt };
            }
        }
        return { found: false, current: '' };
    }""")

    if not sort_state.get("found"):
        return

    current = sort_state.get("current", "")
    if re.search(r"^(alle opmerkingen|all comments)$", current, re.I):
        print(f"   🔽 Comment sort already set to: {GREEN}{current}{RESET}")
        return

    print(f"   🔄 Switching comment sort from '{current}' → 'Alle opmerkingen / All comments'...")
    clicked = page.evaluate(r"""() => {
        const root = window.__fbGetScopeRoot();
        const sortRe = /^(meest relevant|relevantste|populairste|nieuwste|alle opmerkingen|most relevant|top comments|newest|all comments)$/i;
        const btns = Array.from(root.querySelectorAll('[role="button"][aria-haspopup="menu"]'));
        for (const b of btns) {
            const txt = (b.innerText || '').replace(/\s+/g, ' ').trim();
            if (sortRe.test(txt)) {
                b.scrollIntoView({ block: 'center', behavior: 'instant' });
                b.click();
                return true;
            }
        }
        return false;
    }""")

    if not clicked:
        return

    page.wait_for_timeout(700)
    picked = page.evaluate(r"""() => {
        const items = Array.from(document.querySelectorAll('[role="menuitem"], [role="menuitemradio"]'));
        for (const it of items) {
            const txt = (it.innerText || '').toLowerCase();
            if (txt.includes('alle opmerkingen') || txt.includes('all comments')) {
                it.click();
                return txt.split('\n')[0].trim();
            }
        }
        return null;
    }""")

    if picked:
        print(f"   ✅ Sort switched to: {GREEN}{picked}{RESET}")
        page.wait_for_timeout(1200)
        wait_for_spinners_and_mutations(page, 0, timeout_s=5.0)
    else:
        page.keyboard.press("Escape")
        print(f"   {YELLOW}⚠️ 'Alle opmerkingen' option not found in menu; keeping '{current}'.{RESET}")


def expand_visible_waves(page, live_state, max_retries, max_waves, only_near_viewport=True):
    """
    Repeatedly expands 'See more' + reply/load-more buttons in waves,
    syncing newly discovered replies into live_state on every wave.
    """
    total_clicks = 0
    for _ in range(max_waves):
        see_more_clicks = page.evaluate("() => window.__fbExpandSeeMore()")
        if see_more_clicks:
            page.wait_for_timeout(200)

        before_stats = page.evaluate("() => window.__fbHarvest()")
        sync_live_state(page, live_state)

        cands = page.evaluate(r"""(args) => {
            const all = window.__fbFindExpandButtons();
            if (!args.onlyNear) return all;
            const limitY = window.innerHeight + 350;
            const near = all.filter(c => c.top >= -200 && c.top <= limitY);
            return near.length > 0 ? near : all.slice(0, 3);
        }""", {"onlyNear": only_near_viewport})

        if not cands:
            break

        clicked_in_wave = 0
        for c in cands[:3]:
            btn_id = c["btnId"]
            key = c["key"]
            lbl = c["txt"]

            allowed = page.evaluate(r"""(args) => {
                const tries = window.__fbBtnAttempts[args.key] || 0;
                if (tries >= args.maxRetries) {
                    window.__fbDeadButtons[args.key] = true;
                    return false;
                }
                window.__fbBtnAttempts[args.key] = tries + 1;
                const el = document.querySelector(`[data-fb-btn-id="${args.btnId}"]`);
                if (!el) return false;
                el.scrollIntoView({ block: 'center', behavior: 'instant' });
                return true;
            }""", {"key": key, "btnId": btn_id, "maxRetries": max_retries})

            if not allowed:
                continue

            prev_tick = page.evaluate("() => window.__fbMutationTick || 0")
            did_click = False
            try:
                loc = page.locator(f'[data-fb-btn-id="{btn_id}"]').first
                if loc.count() > 0:
                    loc.click(timeout=2000)
                    did_click = True
            except Exception:
                pass

            if not did_click:
                did_click = page.evaluate(r"""(btnId) => {
                    const el = document.querySelector(`[data-fb-btn-id="${btnId}"]`);
                    if (!el) return false;
                    el.click();
                    return true;
                }""", btn_id)

            if did_click:
                clicked_in_wave += 1
                total_clicks += 1
                print(f"      ↳ [Expand] Clicked: {CYAN}{lbl}{RESET}")
                wait_for_spinners_and_mutations(page, prev_tick, timeout_s=4.5)

        if clicked_in_wave == 0:
            break

        page.evaluate("() => window.__fbExpandSeeMore()")
        after_stats = page.evaluate("() => window.__fbHarvest()")
        sync_live_state(page, live_state)

        if after_stats["totalCaptured"] > before_stats["totalCaptured"]:
            diff = after_stats["totalCaptured"] - before_stats["totalCaptured"]
            print(f"      ⚡ [Captured] +{diff} new items (Total: {after_stats['totalCaptured']})")

    return total_clicks


def build_live_result(live_state, url, partial=False, stop_reason=""):
    raw_items = [
        live_state["comments_by_id"][cid]
        for cid in live_state["order"]
        if cid in live_state["comments_by_id"]
    ]
    return {
        "post": live_state["post"] or {},
        "raw_items": raw_items,
        "url": url,
        "partial": partial,
        "stop_reason": stop_reason,
    }


def scrape_facebook_post(url):
    sp = CONFIG["speed_profile"]
    if sp == 1:
        step_ratio, wait_ms, stable_thresh = 0.40, 850, 5
    elif sp == 2:
        step_ratio, wait_ms, stable_thresh = 0.70, 500, 4
    elif sp == 3:
        step_ratio, wait_ms, stable_thresh = 0.90, 300, 3
    else:
        step_ratio = None
        wait_ms = max(150, CONFIG["custom_wait_ms"])
        stable_thresh = max(2, CONFIG["custom_stable_thresh"])

    # Python-side live buffer so closing the browser (X) or Ctrl+C preserves everything scraped so far
    live_state = {
        "post": {},
        "comments_by_id": {},
        "order": [],
    }

    with sync_playwright() as p:
        print(f"\n{CYAN}🖥️  Launching Clean Chrome/Edge...{RESET}")
        browser = launch_browser(p)
        context = create_stealth_context(browser)
        page = context.new_page()

        try:
            print("🌐 Navigating to Facebook URL...")
            page.goto(url, wait_until="domcontentloaded", timeout=120000)

            handle_login_or_consent_if_needed(page, context)

            print("⏳ Waiting for post & comment container to hydrate...")
            deadline = time.time() + 25
            ready = False
            while time.time() < deadline:
                ready = page.evaluate(r"""() => {
                    const dlg = document.querySelector('div[role="dialog"][aria-modal="true"]');
                    const root = dlg || document.querySelector('div[role="main"]');
                    if (!root) return false;
                    return !!(root.querySelector('[role="article"]') || root.querySelector('[data-ad-rendering-role="story_message"]'));
                }""")
                if ready:
                    break
                page.evaluate("() => window.scrollBy(0, 220)")
                page.wait_for_timeout(400)

            if not ready:
                print(f"   {YELLOW}⚠️ No comments detected immediately; attempting engine injection anyway...{RESET}")

            print("👁️  Injecting Facebook DOM Harvester & Wave Engine...")
            page.evaluate(DOM_ENGINE_JS)

            switch_comment_sort_to_all(page)

            page.evaluate("() => window.__fbExpandSeeMore()")
            live_state["post"] = page.evaluate("() => window.__fbExtractPost()")
            page.evaluate("() => window.__fbHarvest()")
            sync_live_state(page, live_state)

            print(f"   📌 Post Author: {BOLD}{live_state['post'].get('author') or 'Unknown'}{RESET} "
                  f"| Advertised comments: {BOLD}{live_state['post'].get('advertisedComments') or '?'}{RESET}")
            print(f"   {DIM}💡 Tip: Closing the browser (X) or pressing Ctrl+C mid-scroll saves everything scraped so far.{RESET}")

            print("🧭 Running Progressive Scroll → Harvest → Expand Wave Loop...")
            scroll_metrics = page.evaluate(r"""() => {
                const sc = window.__fbGetScrollContainer();
                return { h: sc.scrollHeight, ch: sc.clientHeight };
            }""")
            step_px = (
                max(300, int(scroll_metrics["ch"] * step_ratio))
                if step_ratio is not None
                else max(150, CONFIG["custom_step_px"])
            )

            stable = 0
            loop_iter = 0
            last_total = 0

            while stable < stable_thresh:
                loop_iter += 1

                clicks_here = expand_visible_waves(
                    page,
                    live_state,
                    max_retries=CONFIG["max_button_retries"],
                    max_waves=CONFIG["max_waves_per_view"],
                    only_near_viewport=True,
                )

                page.evaluate("() => window.__fbHarvest()")
                sync_live_state(page, live_state)

                prev_tick = page.evaluate("() => window.__fbMutationTick || 0")
                scroll_state = page.evaluate(r"""(stepPx) => {
                    const sc = window.__fbGetScrollContainer();
                    const prevTop = sc.scrollTop;
                    const prevH = sc.scrollHeight;
                    sc.scrollTop = Math.min(sc.scrollTop + stepPx, sc.scrollHeight);
                    const atBottom = sc.scrollTop + sc.clientHeight >= sc.scrollHeight - 60;
                    return {
                        prevTop,
                        newTop: sc.scrollTop,
                        prevH,
                        newH: sc.scrollHeight,
                        atBottom
                    };
                }""", step_px)

                page.wait_for_timeout(wait_ms)
                wait_for_spinners_and_mutations(page, prev_tick, timeout_s=4.0)

                post_scroll_clicks = expand_visible_waves(
                    page,
                    live_state,
                    max_retries=CONFIG["max_button_retries"],
                    max_waves=CONFIG["max_waves_per_view"],
                    only_near_viewport=True,
                )
                after_stats = page.evaluate("() => window.__fbHarvest()")
                sync_live_state(page, live_state)

                spinner_active = page.evaluate("() => window.__fbHasActiveSpinner()")
                rem_btns = page.evaluate("() => window.__fbFindExpandButtons().length")

                if (
                    after_stats["totalCaptured"] == last_total
                    and scroll_state["atBottom"]
                    and not spinner_active
                    and rem_btns == 0
                    and clicks_here == 0
                    and post_scroll_clicks == 0
                ):
                    stable += 1
                    print(f"   ⏳ End check {stable}/{stable_thresh} (Captured: {after_stats['totalCaptured']})")
                else:
                    stable = 0
                    if after_stats["totalCaptured"] != last_total:
                        print(f"   📥 Pass {loop_iter}: {after_stats['totalCaptured']} total comments/replies captured")
                    last_total = after_stats["totalCaptured"]

                if loop_iter > 450:
                    print(f"   {YELLOW}⚠️ Reached safety iteration limit (450). Finalizing...{RESET}")
                    break

            print("🧹 Running final verification sweep for any remaining replies...")
            expand_visible_waves(
                page,
                live_state,
                max_retries=CONFIG["max_button_retries"],
                max_waves=60,
                only_near_viewport=False,
            )

            print("🔬 Finalizing post metadata & comment tree...")
            page.evaluate("() => { window.__fbExpandSeeMore(); window.__fbHarvest(); }")
            live_state["post"] = page.evaluate("() => window.__fbExtractPost()")
            sync_live_state(page, live_state)
            page.evaluate("() => { if (window.__fbObserver) window.__fbObserver.disconnect(); }")

            try:
                save_cumulative_state(context.storage_state())
            except Exception:
                pass

            return build_live_result(live_state, url, partial=False)

        except KeyboardInterrupt:
            reason = "Interrupted by user (Ctrl+C)"
            print(f"\n{YELLOW}⚠️ {reason} — preserving {len(live_state['order'])} items scraped so far...{RESET}")
            if live_state["order"] or live_state["post"].get("text"):
                return build_live_result(live_state, url, partial=True, stop_reason=reason)
            return None

        except Exception as e:
            err_str = str(e)
            is_closed = "closed" in err_str.lower() or "target" in err_str.lower()
            reason = "Browser window closed by user (X)" if is_closed else f"Stopped early ({err_str})"
            print(f"\n{YELLOW}⚠️ {reason}{RESET}")
            if live_state["order"] or live_state["post"].get("text"):
                print(f"   💾 Preserving {len(live_state['order'])} comments/replies scraped so far...")
                return build_live_result(live_state, url, partial=True, stop_reason=reason)
            print(f"{RED}❌ Scrape Error (nothing captured yet):{RESET} {e}")
            return None

        finally:
            try:
                browser.close()
            except Exception:
                pass


# ==========================================
# 🌳 TREE BUILDER & FORMATTER
# ==========================================
def build_comment_tree(raw_items):
    """Assemble top-level comments and nested replies in deterministic discovery order."""
    by_id = {}
    top_level = []
    orphan_replies = []

    for item in raw_items:
        node = dict(item)
        node["replies"] = []
        by_id[node["id"]] = node

    for item in raw_items:
        node = by_id[item["id"]]
        pid = node.get("parentId")
        if node.get("isReply") and pid and pid in by_id and pid != node["id"]:
            by_id[pid]["replies"].append(node)
        elif node.get("isReply") and pid:
            orphan_replies.append(node)
        else:
            top_level.append(node)

    for orph in orphan_replies:
        top_level.append(orph)

    return top_level


def format_txt_archive(data, final_title, method):
    post = data["post"]
    tree = build_comment_tree(data["raw_items"])
    total_items = len(data["raw_items"])
    top_count = sum(1 for x in data["raw_items"] if not x.get("isReply"))
    reply_count = total_items - top_count
    status_line = (
        f"PARTIAL ({data.get('stop_reason', 'Stopped early')})"
        if data.get("partial")
        else "COMPLETE"
    )

    lines = []
    lines.append(f"TITLE: {final_title}")
    lines.append(f"STATUS: {status_line}")
    lines.append(f"METHOD: {method}")
    lines.append(f"URL: {data['url']}")
    lines.append(f"POST AUTHOR: {post.get('author') or 'Unknown'}")
    lines.append(
        f"POST TIME: {post.get('timestampFull') or post.get('timestampShort') or 'Unknown'}"
    )
    lines.append(
        f"COMMENTS: {total_items} captured ({top_count} top-level, {reply_count} replies)"
        + (f" | Facebook advertised: {post['advertisedComments']}" if post.get("advertisedComments") else "")
    )
    lines.append(f"ARCHIVED AT: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    lines.append("=" * 60)
    lines.append("")
    lines.append("### ORIGINAL POST ###")
    lines.append(post.get("text") or "(No text / media only)")
    lines.append("")
    lines.append("=" * 60)
    lines.append("### COMMENTS & REPLIES ###")
    lines.append("")

    for idx, c in enumerate(tree, 1):
        ts = c.get("timestampFull") or c.get("timestampShort") or "?"
        react_str = f" | 👍 {c['reactions']}" if c.get("reactions") else ""
        edit_str = " | [Edited]" if c.get("isEdited") else ""
        orphan_tag = " [Orphan Reply]" if c.get("isReply") else ""

        lines.append(f"[{idx}] {c.get('author') or 'Unknown'}{orphan_tag} ({ts}{react_str}{edit_str})")
        body = c.get("text") or ""
        if body:
            for bline in body.splitlines():
                lines.append(f"    {bline}")
        for att in c.get("attachments", []):
            lines.append(f"    [Attachment: {att.get('alt', 'Media')}] {att.get('src', '')}")

        for r_idx, r in enumerate(c.get("replies", []), 1):
            rts = r.get("timestampFull") or r.get("timestampShort") or "?"
            r_react = f" | 👍 {r['reactions']}" if r.get("reactions") else ""
            r_edit = " | [Edited]" if r.get("isEdited") else ""
            indent = "        " if r.get("depth", 1) >= 2 else "    "
            lines.append(f"{indent}└── [{idx}.{r_idx}] {r.get('author') or 'Unknown'} ({rts}{r_react}{r_edit})")
            rbody = r.get("text") or ""
            if rbody:
                for rline in rbody.splitlines():
                    lines.append(f"{indent}    {rline}")
            for att in r.get("attachments", []):
                lines.append(f"{indent}    [Attachment: {att.get('alt', 'Media')}] {att.get('src', '')}")

        lines.append("-" * 40)

    return "\n".join(lines), tree, top_count, reply_count


# ==========================================
# 📋 MENU ACTIONS
# ==========================================
def action_scrape_facebook():
    print(f"\n{CYAN}{BOLD}🕷️  FACEBOOK COMMENT SCRAPER{RESET}")
    raw_url = input("🔗 Paste Facebook Post / Permalink URL: ")
    target_url = normalize_url(raw_url)
    if not target_url:
        print(f"{YELLOW}⚠️ No URL provided.{RESET}")
        return

    data = scrape_facebook_post(target_url)
    if not data:
        print(f"{RED}❌ Scrape failed (no data captured).{RESET}")
        return

    post = data["post"]
    raw_items = data["raw_items"]
    is_partial = data.get("partial", False)
    final_title, method = resolve_title(post, raw_items)
    txt_output, tree, top_count, reply_count = format_txt_archive(data, final_title, method)

    print(f"\n{BOLD}📋 Preview (First 8 Top-Level Comments):{RESET}")
    for i, c in enumerate(tree[:8], 1):
        r_len = len(c.get("replies", []))
        r_badge = f" {DIM}(+{r_len} replies){RESET}" if r_len else ""
        print(f"   [{i}] {BOLD}{c.get('author')}{RESET}{r_badge}: {RED}{clip(c.get('text'), 110)}{RESET}")

    stamp = datetime.now().strftime("%Y-%m-%d_%H%M")
    partial_tag = " [PARTIAL]" if is_partial else ""
    fname_base = f"{stamp} {safe_filename(final_title)}{partial_tag}"

    txt_path = FB_TXT_DIR / f"{fname_base}.txt"
    txt_path.write_text(txt_output, encoding="utf-8")

    badge = f"{YELLOW}{BOLD}⚠️ PARTIAL ARCHIVE SAVED" if is_partial else f"{GREEN}{BOLD}✅ ARCHIVE SUCCESSFUL"
    print(f"\n{badge}{RESET}")
    if is_partial:
        print(f"  ℹ️  Reason:            {data.get('stop_reason')}")
    print(f"  🏷️  Title ({method}): {final_title}")
    print(f"  📊 Captured:          {len(raw_items)} total ({top_count} top-level, {reply_count} replies)"
          + (f" | FB Advertised: {post['advertisedComments']}" if post.get("advertisedComments") else ""))
    print(f"  📂 TXT Archive:       {txt_path}")

    if CONFIG["save_json"]:
        json_path = FB_JSON_DIR / f"{fname_base}.json"
        json_payload = {
            "title": final_title,
            "status": "PARTIAL" if is_partial else "COMPLETE",
            "stop_reason": data.get("stop_reason") or None,
            "method": method,
            "url": target_url,
            "archived_at": datetime.now().isoformat(),
            "post": post,
            "stats": {
                "total_captured": len(raw_items),
                "top_level": top_count,
                "replies": reply_count,
                "advertised": post.get("advertisedComments"),
            },
            "comments_tree": tree,
        }
        json_path.write_text(json.dumps(json_payload, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"  📂 JSON Archive:      {json_path}")


def action_configure():
    while True:
        print(f"\n{CYAN}{BOLD}⚙️  CONFIGURATION{RESET}")
        print(f"  [1] Speed Profile      (current: {CONFIG['speed_profile']} -> 1=Conservative, 2=Fast, 3=Aggressive, 4=Custom)")
        print(f"  [2] Force 'All Comments' Sort (current: {CONFIG['force_all_comments']})")
        print(f"  [3] Title Mode         (current: {CONFIG['title_mode']} -> 1=Author+Snippet, 2=Gemini API)")
        print(f"  [4] Save JSON Archive  (current: {CONFIG['save_json']})")
        print(f"  [0] Back to Main Menu")

        choice = input("\nSelect option: ").strip()
        if choice == "1":
            val = input("Enter Speed Profile (1-4): ").strip()
            if val in ("1", "2", "3", "4"):
                CONFIG["speed_profile"] = int(val)
                if val == "4":
                    CONFIG["custom_step_px"] = int(input("  Custom step px (e.g. 550): ") or 550)
                    CONFIG["custom_wait_ms"] = int(input("  Custom wait ms (e.g. 500): ") or 500)
                    CONFIG["custom_stable_thresh"] = int(input("  Custom stable checks (e.g. 4): ") or 4)
        elif choice == "2":
            CONFIG["force_all_comments"] = not CONFIG["force_all_comments"]
        elif choice == "3":
            val = input("Title Mode (1=Author+Snippet, 2=Gemini API): ").strip()
            if val in ("1", "2"):
                CONFIG["title_mode"] = int(val)
        elif choice == "4":
            CONFIG["save_json"] = not CONFIG["save_json"]
        elif choice == "0" or choice == "":
            break


def main():
    while True:
        print_banner()
        print(f"  {BOLD}[1]{RESET} 🕷️  Scrape Facebook Post & Comments")
        print(f"  {BOLD}[2]{RESET} 🔐 Authenticate / Refresh Browser Session (Cumulative)")
        print(f"  {BOLD}[3]{RESET} ⚙️  Settings & Speed Profiles")
        print(f"  {BOLD}[0]{RESET} 🚪 Exit")

        choice = input(f"\n{BOLD}Select an option [1/2/3/0]: {RESET}").strip()

        if choice == "1":
            action_scrape_facebook()
            input(f"\n{DIM}Press [Enter] to return to menu...{RESET}")
        elif choice == "2":
            run_auth_flow()
            input(f"\n{DIM}Press [Enter] to return to menu...{RESET}")
        elif choice == "3":
            action_configure()
        elif choice == "0":
            print("👋 Goodbye!")
            break
        else:
            print(f"{YELLOW}Invalid option. Please choose 1, 2, 3, or 0.{RESET}")


if __name__ == "__main__":
    main()