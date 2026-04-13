#!/usr/bin/env python3
import json
import os
import re
import subprocess
import sys
import threading
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.error import URLError
from urllib.request import Request, urlopen

BASE_DIR = Path(__file__).resolve().parent
STATE_PATH = BASE_DIR / "state.json"
LLM_SELECTION_PATH = BASE_DIR / "llm_selection.json"
CLASSIC_DIR = BASE_DIR.parent

agent_lock = threading.Lock()
agent_process = None
autogpt_lock = threading.Lock()
autogpt_process = None

PROVIDER_PRIORITY = ["openrouter", "groq", "together", "huggingface", "openai"]

PROVIDER_DEFAULT_BEST_MODEL = {
    "openrouter": "anthropic/claude-3.7-sonnet",
    "groq": "llama-3.3-70b-versatile",
    "together": "meta-llama/Llama-3.3-70B-Instruct-Turbo",
    "huggingface": "mistralai/Mistral-7B-Instruct-v0.3",
    "openai": "gpt-4o-mini",
}

PROVIDER_MODEL_CATALOG = {
    "openrouter": [
        "anthropic/claude-3.7-sonnet",
        "google/gemini-2.5-pro",
        "openai/gpt-4o",
        "openai/gpt-4o-mini",
        "meta-llama/llama-3.3-70b-instruct",
    ],
    "groq": [
        "llama-3.3-70b-versatile",
        "llama-3.1-70b-versatile",
        "llama-3.1-8b-instant",
        "mixtral-8x7b-32768",
    ],
    "together": [
        "meta-llama/Llama-3.3-70B-Instruct-Turbo",
        "meta-llama/Llama-3-70b-chat-hf",
        "mistralai/Mixtral-8x7B-Instruct-v0.1",
    ],
    "huggingface": [
        "mistralai/Mistral-7B-Instruct-v0.3",
        "mistralai/Mixtral-8x7B-Instruct-v0.1",
        "meta-llama/Llama-3-8b-chat-hf",
    ],
    "openai": [
        "gemini-2.5-flash",
        "gemini-2.5-pro",
        "gpt-4o",
        "gpt-4o-mini",
    ],
}


def best_model_for_provider(provider, provider_cfg=None):
    if provider == "openai":
        # Support OpenAI-compatible endpoints that use non-OpenAI model IDs (e.g. Gemini).
        return (
            os.environ.get("OPENAI_MODEL")
            or os.environ.get("SMART_LLM")
            or os.environ.get("FAST_LLM")
            or (provider_cfg or {}).get("default_model")
            or PROVIDER_DEFAULT_BEST_MODEL["openai"]
        )
    if provider == "together":
        return (
            os.environ.get("TOGETHER_MODEL")
            or (provider_cfg or {}).get("default_model")
            or PROVIDER_DEFAULT_BEST_MODEL.get("together", "meta-llama/Llama-3.3-70B-Instruct-Turbo")
        )
    if provider == "huggingface":
        return (
            os.environ.get("HF_MODEL")
            or (provider_cfg or {}).get("default_model")
            or PROVIDER_DEFAULT_BEST_MODEL.get("huggingface", "mistralai/Mistral-7B-Instruct-v0.3")
        )
    return (
        (provider_cfg or {}).get("default_model")
        or PROVIDER_DEFAULT_BEST_MODEL.get(provider)
        or "best"
    )


def load_env_file(path):
    if not path.exists():
        return
    try:
        for raw_line in path.read_text(encoding="utf-8").splitlines():
            line = raw_line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            key = key.strip()
            value = value.strip().strip('"').strip("'")
            if key and key not in os.environ:
                os.environ[key] = value
    except Exception:
        # Keep server resilient even if env file contains malformed lines.
        pass


def bootstrap_env():
    # Priority: already-exported env vars > classic/.env > agent_ui/.env
    load_env_file(CLASSIC_DIR / ".env")
    load_env_file(BASE_DIR / ".env")


bootstrap_env()


def get_configured_providers():
    configured = {}
    if os.environ.get("OPENROUTER_API_KEY"):
        configured["openrouter"] = {
            "provider": "openrouter",
            "base_url": os.environ.get("OPENROUTER_BASE_URL")
            or os.environ.get("OPENROUTER_API_BASE_URL")
            or "https://openrouter.ai/api/v1",
            "api_key": os.environ.get("OPENROUTER_API_KEY"),
            "default_model": os.environ.get("OPENROUTER_MODEL", "openai/gpt-4o-mini"),
        }
    if os.environ.get("GROQ_API_KEY"):
        configured["groq"] = {
            "provider": "groq",
            "base_url": os.environ.get("GROQ_BASE_URL")
            or os.environ.get("GROQ_API_BASE_URL")
            or "https://api.groq.com/openai/v1",
            "api_key": os.environ.get("GROQ_API_KEY"),
            "default_model": os.environ.get("GROQ_MODEL", "llama-3.1-70b-versatile"),
        }
    if os.environ.get("TOGETHER_API_KEY"):
        configured["together"] = {
            "provider": "together",
            "base_url": os.environ.get("TOGETHER_BASE_URL")
            or os.environ.get("TOGETHER_API_BASE_URL")
            or "https://api.together.xyz/v1",
            "api_key": os.environ.get("TOGETHER_API_KEY"),
            "default_model": os.environ.get("TOGETHER_MODEL", "meta-llama/Llama-3.3-70B-Instruct-Turbo"),
        }
    if os.environ.get("HF_API_KEY"):
        configured["huggingface"] = {
            "provider": "huggingface",
            "base_url": os.environ.get("HF_BASE_URL")
            or os.environ.get("HF_API_BASE_URL")
            or "https://api-inference.huggingface.co/v1",
            "api_key": os.environ.get("HF_API_KEY"),
            "default_model": os.environ.get("HF_MODEL", "mistralai/Mistral-7B-Instruct-v0.3"),
        }
    if os.environ.get("OPENAI_API_KEY"):
        configured["openai"] = {
            "provider": "openai",
            "base_url": os.environ.get("OPENAI_BASE_URL")
            or os.environ.get("OPENAI_API_BASE_URL")
            or "https://api.openai.com/v1",
            "api_key": os.environ.get("OPENAI_API_KEY"),
            "default_model": os.environ.get("OPENAI_MODEL")
            or os.environ.get("SMART_LLM")
            or os.environ.get("FAST_LLM")
            or "gpt-4o-mini",
        }
    return configured


def get_llm_config():
    return get_effective_llm_config()


def read_llm_selection():
    if not LLM_SELECTION_PATH.exists():
        return {}
    try:
        data = json.loads(LLM_SELECTION_PATH.read_text(encoding="utf-8"))
        if isinstance(data, dict):
            return data
    except Exception:
        pass
    return {}


def write_llm_selection(provider, model):
    payload = {"provider": provider, "model": model}
    LLM_SELECTION_PATH.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def preferred_provider(configured):
    for provider in PROVIDER_PRIORITY:
        if provider in configured:
            return provider
    return None


def get_effective_llm_config():
    configured = get_configured_providers()
    if not configured:
        return None

    selection = read_llm_selection()
    selected_provider = selection.get("provider")
    selected_model = selection.get("model", "best")

    provider = selected_provider if selected_provider in configured else preferred_provider(configured)
    if not provider:
        return None

    cfg = dict(configured[provider])

    # If selected provider is unavailable, ignore incompatible selected model.
    if selected_provider and selected_provider != provider:
        selected_model = "best"

    if selected_model == "best":
        model = best_model_for_provider(provider, cfg)
    else:
        model = selected_model

    cfg["model"] = model
    cfg["selected_model"] = selected_model
    cfg["selected_provider"] = provider
    return cfg


def fetch_remote_models(cfg):
    req = Request(
        f"{cfg['base_url'].rstrip('/')}/models",
        headers={"Authorization": f"Bearer {cfg['api_key']}"},
    )
    try:
        with urlopen(req, timeout=10) as resp:
            body = json.loads(resp.read().decode("utf-8", errors="ignore"))
        items = body.get("data", []) if isinstance(body, dict) else []
        models = [x.get("id") for x in items if isinstance(x, dict) and x.get("id")]
        return models[:100]
    except Exception:
        return []


def get_llm_options_payload():
    configured = get_configured_providers()
    selection = read_llm_selection()
    selected_provider = selection.get("provider")
    selected_model = selection.get("model", "best")
    providers = []

    for provider in PROVIDER_PRIORITY:
        cfg = configured.get(provider)
        models = ["best"]
        models.extend(PROVIDER_MODEL_CATALOG.get(provider, []))
        if cfg:
            models.extend(fetch_remote_models(cfg))
        deduped = []
        seen = set()
        for m in models:
            if m and m not in seen:
                deduped.append(m)
                seen.add(m)

        providers.append(
            {
                "provider": provider,
                "configured": provider in configured,
                "defaultBestModel": best_model_for_provider(provider, configured.get(provider)),
                "models": deduped,
            }
        )

    effective = get_effective_llm_config()
    return {
        "providers": providers,
        "selectedProvider": selected_provider,
        "selectedModel": selected_model,
        "effectiveProvider": effective.get("provider") if effective else None,
        "effectiveModel": effective.get("model") if effective else None,
    }


def set_llm_selection(provider, model):
    configured = get_configured_providers()
    if provider not in PROVIDER_PRIORITY:
        return False, "Provider invalid"

    allowed = set(["best"] + PROVIDER_MODEL_CATALOG.get(provider, []))
    if provider in configured:
        remote = fetch_remote_models(configured[provider])
        allowed.update(remote)

    if model not in allowed:
        return False, f"Modelul {model} nu este disponibil pentru providerul {provider}."

    write_llm_selection(provider, model)

    if provider not in configured:
        best = best_model_for_provider(provider)
        chosen = best if model == "best" else model
        return (
            True,
            f"Selectia a fost salvata: {provider} / {chosen}. "
            f"Providerul nu e configurat inca (lipseste API key), deci raman pe fallback local pana il configurezi.",
        )

    effective = get_effective_llm_config()
    eff_provider = effective.get("provider") if effective else provider
    eff_model = effective.get("model") if effective else model
    return True, f"Selectie LLM actualizata: {eff_provider} / {eff_model}"


def ask_llm(user_message, state):
    cfg = get_llm_config()
    if not cfg:
        return {
            "error": "no_api_key",
            "message": None,
            "detail": "Nu exista cheie API configurata. Seteaza OPENROUTER_API_KEY, GROQ_API_KEY sau OPENAI_API_KEY."
        }

    status, logs, stats, results, last_log, count = summarize_state(state)
    system_prompt = (
        "You are an assistant inside an AutoGPT dashboard. "
        "Answer in Romanian, concise and practical. "
        "Use current runtime state when relevant."
    )
    context = (
        f"State summary: status={status}, results_count={len(results)}, "
        f"stats={json.dumps(stats, ensure_ascii=False)}, last_log={last_log}"
    )
    payload = {
        "model": cfg["model"],
        "temperature": 0.3,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "system", "content": context},
            {"role": "user", "content": user_message},
        ],
    }

    data = json.dumps(payload).encode("utf-8")
    url = f"{cfg['base_url'].rstrip('/')}/chat/completions"
    req = Request(
        url,
        data=data,
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {cfg['api_key']}",
        },
    )

    try:
        with urlopen(req, timeout=20) as resp:
            body = json.loads(resp.read().decode("utf-8", errors="ignore"))
        
        # Check for API error in response
        if "error" in body:
            error_msg = body.get("error", {})
            if isinstance(error_msg, dict):
                error_msg = error_msg.get("message", str(error_msg))
            return {
                "error": "api_error",
                "message": None,
                "detail": f"API error ({cfg['provider']}/{cfg['model']}): {error_msg}"
            }
        
        content = body.get("choices", [{}])[0].get("message", {}).get("content")
        if content:
            return {
                "error": None,
                "message": content,
                "detail": f"Response from {cfg['provider']}/{cfg['model']}"
            }
        return {
            "error": "empty_response",
            "message": None,
            "detail": "API response was empty or malformed"
        }
    except TimeoutError as e:
        return {
            "error": "timeout",
            "message": None,
            "detail": f"API call timed out after 20s to {cfg['provider']}"
        }
    except URLError as e:
        return {
            "error": "network_error",
            "message": None,
            "detail": f"Network error calling {cfg['provider']}: {str(e)}"
        }
    except json.JSONDecodeError as e:
        return {
            "error": "json_decode_error",
            "message": None,
            "detail": f"API returned invalid JSON: {str(e)}"
        }
    except Exception as e:
        return {
            "error": "unknown",
            "message": None,
            "detail": f"Unexpected error: {type(e).__name__}: {str(e)}"
        }


def read_state():
    if STATE_PATH.exists():
        try:
            return json.loads(STATE_PATH.read_text(encoding="utf-8"))
        except Exception:
            return {}
    return {}


def write_state(state):
    STATE_PATH.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")


def reset_state():
    write_state(
        {
            "status": "idle",
            "logs": [],
            "results": [],
            "stats": {},
            "started_at": None,
            "finished_at": None,
        }
    )


def is_agent_running():
    global agent_process
    return agent_process is not None and agent_process.poll() is None


def is_autogpt_running():
    global autogpt_process
    return autogpt_process is not None and autogpt_process.poll() is None


def start_autogpt_ui():
    global autogpt_process
    with autogpt_lock:
        if is_autogpt_running():
            return False, "UI oficial AutoGPT ruleaza deja pe portul 8000"

        autogpt_process = subprocess.Popen(
            ["poetry", "run", "serve", "--debug"],
            cwd=str(CLASSIC_DIR),
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        return True, "UI oficial AutoGPT a fost pornit"


def stop_autogpt_ui():
    global autogpt_process
    with autogpt_lock:
        if not is_autogpt_running():
            return False, "UI oficial AutoGPT nu ruleaza"

        try:
            autogpt_process.terminate()
            autogpt_process.wait(timeout=8)
        except Exception:
            try:
                autogpt_process.kill()
            except Exception:
                pass

        return True, "UI oficial AutoGPT a fost oprit"


def stop_all_services():
    messages = []
    any_action = False

    if is_agent_running():
        ok, msg = stop_agent()
        messages.append(msg)
        any_action = any_action or ok
    else:
        messages.append("Agentul era deja oprit")

    if is_autogpt_running():
        ok, msg = stop_autogpt_ui()
        messages.append(msg)
        any_action = any_action or ok
    else:
        messages.append("UI oficial AutoGPT era deja oprit")

    return True, " | ".join(messages) if messages else "Nimic de oprit"


def start_agent(task_text):
    global agent_process
    with agent_lock:
        if is_agent_running():
            return False, "Agentul ruleaza deja"

        env = os.environ.copy()
        if task_text:
            env["AGENT_QUERY"] = task_text

        reset_state()
        agent_process = subprocess.Popen(
            [sys.executable, str(BASE_DIR / "run_publi24_agent.py")],
            cwd=str(BASE_DIR),
            env=env,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        return True, "Agent pornit"


def stop_agent():
    global agent_process
    with agent_lock:
        if not is_agent_running():
            return False, "Agentul nu ruleaza"

        try:
            agent_process.terminate()
            agent_process.wait(timeout=5)
        except Exception:
            try:
                agent_process.kill()
            except Exception:
                pass

        state = read_state()
        if isinstance(state, dict):
            state["status"] = "stopped"
            logs = state.get("logs", [])
            logs.append("[SYSTEM] Agent oprit manual din dashboard")
            state["logs"] = logs[-300:]
            write_state(state)

        return True, "Agent oprit"


def summarize_state(state):
    status = state.get("status", "idle")
    logs = state.get("logs", []) or []
    stats = state.get("stats", {}) or {}
    results = state.get("results", []) or []

    last_log = logs[-1] if logs else "fara loguri"
    count = stats.get("count", len(results)) if isinstance(stats, dict) else len(results)
    return status, logs, stats, results, last_log, count


def build_llm_identity_reply():
    cfg = get_effective_llm_config()
    if not cfg:
        selection = read_llm_selection()
        sel_provider = selection.get("provider")
        sel_model = selection.get("model", "best")
        if sel_provider in PROVIDER_PRIORITY:
            shown_model = (
                best_model_for_provider(sel_provider)
                if sel_model == "best"
                else sel_model
            )
            return (
                f"Selectia curenta este salvata: {sel_provider} / {shown_model}, "
                "dar providerul nu este configurat (lipseste API key). "
                "Momentan functionez in fallback local bazat pe starea agentului."
            )
        return (
            "Nu am un LLM extern activ acum. Functionez in fallback local bazat pe starea agentului. "
            "Daca vrei raspunsuri LLM reale, seteaza OPENROUTER_API_KEY, GROQ_API_KEY sau OPENAI_API_KEY."
        )
    return (
        f"LLM activ: provider={cfg.get('provider')} | model={cfg.get('model')} "
        "(live, din configuratia backend)."
    )


def build_domain_fallback_reply(raw, msg_words):
    raw_lower = raw.lower()

    # Historical person facts (including common typo variants in Romanian prompts).
    if "alexandru" in msg_words and "cuza" in msg_words:
        if any(token in raw_lower for token in ["murit", "a murit", "mrint", "morit", "deces", "decedat"]):
            return "Alexandru Ioan Cuza a murit in anul 1873 (15 mai 1873)."
        if "nascut" in raw_lower or "naster" in raw_lower:
            return "Alexandru Ioan Cuza s-a nascut in 1820 (20 martie 1820)."
        return "Alexandru Ioan Cuza a fost domnitorul Unirii Principatelor (1859) si a murit in 1873."

    # Science & math
    if "fotosinteza" in msg_words:
        return "Fotosinteza este procesul prin care plantele transforma lumina, apa si CO2 in glucoza si oxigen. Are loc in cloroplaste si sustine lantul trofic prin producerea biomasei."
    if "adn" in msg_words and "arn" in msg_words:
        return "ADN stocheaza informatia genetica pe termen lung, iar ARN o foloseste temporar in sinteza proteinelor. ADN este de obicei dublu catenar, ARN este in general monocatenar."
    if "entropia" in msg_words:
        return "Entropia masoara gradul de dezordine al unui sistem si directia naturala a proceselor spontane. In termodinamica, pentru sisteme izolate, entropia tinde sa creasca."
    if "prim" in msg_words and "numar" in msg_words:
        return "Un numar prim este un numar natural mai mare ca 1 care are exact doi divizori pozitivi: 1 si el insusi. Exemple: 2, 3, 5, 7, 11."
    if "derivata" in msg_words and "x" in raw.lower():
        return "Derivata lui x^2 este 2x. Regula folosita este regula puterii: d/dx(x^n) = n*x^(n-1)."
    if "integrala" in msg_words and "definita" in msg_words:
        return "Integrala definita masoara aria semnata sub graficul unei functii pe un interval [a, b]. Se noteaza integrală de la a la b si este legata de primitiva prin teorema fundamentala a analizei."

    # Finance
    if "dobanda" in msg_words and "compusa" in msg_words:
        return "Dobanda compusa inseamna ca dobanda se adauga la principal si produce la randul ei dobanda. Formula de baza este A = P(1 + r/n)^(n*t)."
    if "inflatia" in msg_words:
        return "Inflatia este cresterea generalizata a preturilor in timp, ceea ce reduce puterea de cumparare a banilor. Se monitorizeaza de obicei prin indici precum IPC/CPI."
    if "buget" in msg_words:
        return "Pentru buget lunar: listeaza veniturile nete, fixeaza cheltuielile fixe, pune o suma pentru economii, apoi limite pentru cheltuieli variabile. O regula simpla este 50/30/20."
    if "etf" in msg_words:
        return "ETF este un fond tranzactionat la bursa care urmareste un indice sau o tema. Ofera diversificare rapida si costuri de administrare, de regula, mai mici decat fondurile active."
    if "actiuni" in msg_words and "obligatiuni" in msg_words:
        return "Actiunile ofera participare la capital si potential de crestere mai mare, dar cu volatilitate ridicata. Obligatiunile sunt datorie, cu fluxuri mai previzibile si risc in general mai mic."
    if "cash" in msg_words and "flow" in msg_words:
        return "Cash-flow-ul reprezinta intrarile si iesirile efective de numerar dintr-o afacere. Este critic pentru lichiditate, chiar daca firma este profitabila contabil."
    if "lichiditate" in msg_words:
        return "Lichiditatea este capacitatea de a transforma rapid activele in bani fara pierderi mari de valoare. La nivel de firma, mai inseamna si capacitatea de a plati obligatiile la timp."
    if "p/e" in raw.lower() or ("ratio" in msg_words and "p" in msg_words):
        return "P/E (price-to-earnings) compara pretul unei actiuni cu profitul pe actiune. Un P/E mare poate indica asteptari ridicate de crestere sau supraevaluare."
    if "amortizarea" in msg_words:
        return "Amortizarea contabila este repartizarea costului unui activ pe durata sa de utilizare. Ea reduce profitul contabil periodic, fara iesire directa de numerar in acel moment."

    # Tech & AI
    if "ram" in msg_words and "ssd" in msg_words:
        return "RAM este memorie volatila, foarte rapida, folosita temporar de programe in executie. SSD este stocare persistenta, mai lenta decat RAM, dar pastreaza datele fara curent."
    if "api" in msg_words and "rest" in msg_words:
        return "Un API REST expune resurse prin endpoint-uri HTTP si foloseste de obicei metodele GET/POST/PUT/DELETE. Datele sunt transferate frecvent in JSON."
    if "index" in msg_words and "baze" in msg_words:
        return "Un index in baza de date accelereaza cautarea pe coloane, similar unui cuprins. Costul este spatiu suplimentar si scrieri ceva mai lente la insert/update."
    if "n" in msg_words and "log" in msg_words:
        return "Complexitatea O(n log n) apare frecvent la algoritmi eficienti de sortare (ex: merge sort). E mai buna decat O(n^2) pentru seturi mari de date."
    if "docker" in msg_words:
        return "Docker impacheteaza aplicatia si dependintele ei in containere portabile. Astfel, ruleaza mai predictibil intre medii diferite (dev/test/prod)."
    if "retea" in msg_words and "neuronala" in msg_words:
        return "O retea neuronala este un model format din straturi de neuroni artificiali care invata tipare din date. Invatarea se face prin ajustarea ponderilor pe baza erorii."
    if "supervised" in msg_words or "unsupervised" in msg_words:
        return "In supervised learning ai date etichetate (input + raspuns corect), iar in unsupervised modelul cauta structuri fara etichete (clustering, reducere dimensionala)."
    if "overfitting" in msg_words:
        return "Overfitting apare cand modelul invata prea bine datele de antrenare, inclusiv zgomotul, si generalizeaza slab pe date noi. Solutii: regularizare, mai multe date, validare corecta."
    if "backpropagation" in msg_words:
        return "Backpropagation calculeaza gradientii erorii de la iesire spre intrare si actualizeaza ponderile modelului. Este mecanismul central de antrenare pentru retele neuronale clasice."
    if "embeddings" in msg_words:
        return "Embeddings sunt reprezentari vectoriale dense ale textului/obiectelor, unde similaritatea semantica devine distanta geometrica. Sunt utile la cautare semantica si clustering."

    # Health
    if "hipertensiune" in msg_words:
        return "Hipertensiunea arteriala inseamna valori tensionale crescute persistent si creste riscul cardiovascular. Monitorizarea regulata si evaluarea medicala sunt esentiale."
    if "deshidratarea" in msg_words:
        return "Semne de deshidratare usoara: sete, gura uscata, urina mai concentrata, oboseala, durere de cap usoara. Rehidratarea se face gradual cu apa si electroliti."
    if "deficit" in msg_words and "caloric" in msg_words:
        return "Deficitul caloric inseamna aport energetic mai mic decat consumul zilnic, ceea ce duce in timp la scadere in greutate. Un ritm sustenabil este de obicei un deficit moderat."
    if "virus" in msg_words and "bacterie" in msg_words:
        return "Virusurile au nevoie de celule gazda pentru a se multiplica, bacteriile sunt organisme vii unicelulare. Antibioticele actioneaza pe bacterii, nu pe virusi."
    if "insulina" in msg_words:
        return "Insulina este hormonul care ajuta glucoza sa intre in celule si regleaza glicemia. Deficitul sau rezistenta la insulina sunt implicate in diabet."

    # Cooking
    if "orez" in msg_words:
        return "Pentru orez simplu: clateste, foloseste aproximativ 1 parte orez la 1.8-2 parti apa, fierbe la foc mic cu capac 12-15 minute, apoi lasa-l 5 minute la odihna."
    if "omleta" in msg_words:
        return "Omleta pufoasa: bate 2-3 oua cu putin lapte/apa, sare, piper, tigaie incinsa cu unt, foc mediu-mic, amestec scurt la inceput apoi pliaza cand e inca usor cremoasa."
    if "paine" in msg_words:
        return "Paine fara masina: faina, apa, drojdie, sare, framantare 8-10 minute, dospire 1-2 ore, modelare, a doua dospire scurta, coacere la 220C cu abur la inceput."
    if "condimente" in msg_words and "pui" in msg_words:
        return "La pui la cuptor merg bine: paprika, usturoi, piper, cimbru/rozmarin, putina lamaie si ulei de masline. Marineaza 30-60 minute pentru gust mai bun."
    if "salata" in msg_words and "crocanta" in msg_words:
        return "Pastreaza salata crocanta prin spalare rapida, uscare foarte buna, depozitare la frigider intr-o cutie cu servet absorbant schimbat periodic."

    # History / civics
    if "primul" in msg_words and "razboi" in msg_words and "mondial" in msg_words:
        return "Primul Razboi Mondial a fost declansat de un cumul de factori: aliante rigide, nationalism, rivalitati imperiale si militarizare, cu atentatul de la Sarajevo drept scanteie."
    if "renasterea" in msg_words:
        return "Renasterea a fost o miscare culturala europeana (sec. XIV-XVII) care a revitalizat interesul pentru arta, stiinta si textele antice, cu accent pe umanism."
    if "marie" in msg_words and "curie" in msg_words:
        return "Marie Curie a fost fizician/chimist pionier in studiul radioactivitatii si prima persoana premiata Nobel de doua ori, in doua stiinte diferite."
    if "razboiul" in msg_words and "rece" in msg_words:
        return "Razboiul Rece a fost confruntarea geopolitica dintre SUA si URSS dupa 1945, caracterizata de competitie ideologica, cursa inarmarii si razboaie prin intermediari."
    if "revolutia" in msg_words and "industriala" in msg_words:
        return "Revolutia Industriala a adus mecanizare, productie in fabrici si crestere economica accelerata, transformand profund munca, urbanizarea si structura sociala."
    if "statul" in msg_words and "drept" in msg_words:
        return "Statul de drept inseamna ca toti, inclusiv autoritatile, sunt supusi legii, cu institutii independente si protectia efectiva a drepturilor fundamentale."
    if "democratie" in msg_words and "autoritarism" in msg_words:
        return "Democratia presupune pluralism, alegeri libere si separatia puterilor. Autoritarismul concentreaza puterea, limiteaza opozitia si reduce controlul institutional."
    if "separatia" in msg_words and "puterilor" in msg_words:
        return "Separatia puterilor imparte autoritatea statului in legislativa, executiva si judecatoreasca pentru a preveni abuzul si a crea mecanisme de control reciproc."
    if "prezumtia" in msg_words and "nevinovatie" in msg_words:
        return "Prezumtia de nevinovatie inseamna ca o persoana este considerata nevinovata pana la dovedirea vinovatiei printr-o hotarare definitiva."
    if "constitutie" in msg_words:
        return "Constitutia este legea fundamentala care stabileste organizarea statului, drepturile cetatenilor si limitele exercitarii puterii publice."

    # Psychology / productivity
    if "rezilienta" in msg_words:
        return "Rezilienta emotionala este capacitatea de a reveni functional dupa stres sau esec. Se construieste prin rutine, sprijin social, reinterpretare cognitiva si odihna adecvata."
    if "procrastinarea" in msg_words:
        return "Pentru procrastinare: sparge sarcina in pasi de 5-10 minute, incepe cu cel mai mic pas, elimina distractorii si foloseste blocuri scurte tip Pomodoro."
    if "ascultarea" in msg_words and "activa" in msg_words:
        return "Ascultarea activa inseamna atentie reala, clarificari, parafrazare si validarea interlocutorului fara a intrerupe sau judeca prematur."
    if "smart" in msg_words and "obiectiv" in msg_words:
        return "Un obiectiv SMART este Specific, Masurabil, Abordabil, Relevant si incadrat in Timp. Exemplu: 'Invat 30 min zilnic, 5 zile/saptamana, timp de 8 saptamani'."
    if "burnout" in msg_words:
        return "Burnout-ul este epuizare cronica fizica si emotionala asociata stresului prelungit. Semnele includ oboseala persistenta, cinism si scaderea performantei."

    return None


def make_chat_reply(message):
    raw = (message or "").strip()
    msg = raw.lower()
    msg_words = set(re.findall(r"[a-z0-9]+", msg))
    state = read_state()
    if not isinstance(state, dict):
        state = {}

    status, logs, stats, results, last_log, count = summarize_state(state)

    if any(phrase in msg for phrase in ["ce faci", "ce faci acum", "ce se intampla", "acum ce", "what are you doing"]):
        if status == "running":
            return (
                f"Acum rulez cautarea in fundal. Am {count} rezultate pana acum. "
                f"Ultimul update: {last_log}"
            )
        if status == "done":
            return (
                f"Acum sunt in stare finalizata. Rezultate colectate: {count}. "
                "Poti porni o noua rulare cu Start Agent daca vrei."
            )
        if status == "stopped":
            return "Acum sunt oprit. Daca vrei, pornesc din nou imediat cu Start Agent."
        if status == "error":
            return f"Acum sunt in eroare. Ultimul mesaj util: {last_log}"
        return "Acum astept o comanda noua. Pot porni agentul, opri agentul sau rezuma starea curenta."

    if any(word in msg_words for word in ["salut", "hello", "buna", "hi"]):
        return "Salut. Pot sa-ti spun ce face agentul acum, status, loguri, rezultate si statistici."

    if any(word in msg for word in ["llm", "model", "language", "alnguage", "limbaj", "gpt", "openai", "openrouter", "groq"]):
        return build_llm_identity_reply()

    if any(word in msg for word in ["status", "merge", "state", "stare"]):
        return f"Status curent agent: {status}. Rezultate: {count}."

    if any(word in msg for word in ["log", "logs", "jurnal", "ultim"]):
        if not logs:
            return "Nu exista loguri inca."
        tail = " | ".join(logs[-2:])
        return f"Ultimele loguri: {tail}"

    if any(word in msg for word in ["rezultat", "results", "anunt", "listing"]):
        if not results:
            return "Nu am rezultate momentan. Incearca Start Agent sau alta interogare."
        preview = "; ".join((x.get("title", "-")[:60] for x in results[:3]))
        return f"Am {len(results)} rezultate. Primele: {preview}"

    if any(word in msg for word in ["stat", "medie", "median", "min", "max", "pret"]):
        if not stats or stats.get("count", 0) == 0:
            return "Statistici indisponibile momentan (count=0)."
        return (
            "Statistici curente: "
            f"count={stats.get('count')}, min={stats.get('min')}, "
            f"max={stats.get('max')}, mean={stats.get('mean')}, median={stats.get('median')}"
        )

    if "start" in msg or "porn" in msg:
        return "Pentru pornire foloseste butonul Start Agent (sau endpoint-ul /api/start)."

    if "stop" in msg or "opreste" in msg:
        return "Pentru oprire foloseste butonul Stop (sau endpoint-ul /api/stop)."

    if "help" in msg or "ajutor" in msg:
        return "Poti intreba: ce faci acum, status, ultimele loguri, rezultate, statistici sau poti cere pornire/oprire." 

    if not raw:
        return "Mesaj gol primit. Scrie, de exemplu: ce faci acum?"

    # Pentru mesaje generale, folosim LLM real daca exista cheie configurata.
    llm_result = ask_llm(raw, state)
    
    # Check if we got a successful response
    if llm_result and not llm_result.get("error"):
        return llm_result.get("message", "").strip()
    
    # If LLM call failed, report the error clearly
    if llm_result and llm_result.get("error"):
        error_detail = llm_result.get("detail", "Unknown error")
        return f"❌ LLM Error: {error_detail}"
    
    # Fallback contextual, nu raspuns generic fix.
    if status == "running":
        return (
            f"Am inteles. Agentul ruleaza acum si ultimul update este: {last_log}. "
            "Daca vrei, iti detaliez logurile sau statisticile."
        )
    return (
        "Nu am putut obtine raspunsul de la modelul extern in acest moment. "
        "Incearca sa reformulezi intrebarea sau selecteaza alt model din iconita LLM. "
        f"Status agent: {status}, rezultate: {count}."
    )


class Handler(SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(BASE_DIR), **kwargs)

    def _json(self, code, payload):
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.path.startswith("/health"):
            cfg = get_effective_llm_config()
            selection = read_llm_selection()
            self._json(
                200,
                {
                    "ok": True,
                    "agentRunning": is_agent_running(),
                    "autogptRunning": is_autogpt_running(),
                    "llmProvider": cfg["provider"] if cfg else None,
                    "llmModel": cfg["model"] if cfg else None,
                    "llmSelectedProvider": selection.get("provider"),
                    "llmSelectedModel": selection.get("model", "best"),
                },
            )
            return
        if self.path.startswith("/api/llm/options"):
            self._json(200, {"ok": True, **get_llm_options_payload()})
            return
        return super().do_GET()

    def do_POST(self):
        length = int(self.headers.get("Content-Length", "0"))
        raw = self.rfile.read(length) if length else b"{}"

        try:
            data = json.loads(raw.decode("utf-8") or "{}")
        except Exception:
            data = {}

        if self.path == "/api/start":
            task = data.get("task") if isinstance(data, dict) else None
            ok, message = start_agent(task)
            self._json(200 if ok else 409, {"ok": ok, "message": message})
            return

        if self.path == "/api/stop":
            ok, message = stop_agent()
            self._json(200 if ok else 409, {"ok": ok, "message": message})
            return

        if self.path == "/api/clear":
            if is_agent_running():
                stop_agent()
            reset_state()
            self._json(200, {"ok": True, "message": "Datele au fost resetate"})
            return

        if self.path == "/api/chat":
            text = data.get("message") if isinstance(data, dict) else ""
            reply = make_chat_reply(text)
            self._json(200, {"ok": True, "reply": reply})
            return

        if self.path == "/api/llm/select":
            provider = data.get("provider") if isinstance(data, dict) else None
            model = data.get("model") if isinstance(data, dict) else None
            ok, message = set_llm_selection(provider, model)
            self._json(200 if ok else 409, {"ok": ok, "message": message})
            return

        if self.path == "/api/autogpt/start":
            ok, message = start_autogpt_ui()
            self._json(200 if ok else 409, {"ok": ok, "message": message})
            return

        if self.path == "/api/autogpt/stop":
            ok, message = stop_autogpt_ui()
            self._json(200 if ok else 409, {"ok": ok, "message": message})
            return

        if self.path == "/api/stop-all":
            ok, message = stop_all_services()
            self._json(200 if ok else 409, {"ok": ok, "message": message})
            return

        self._json(404, {"ok": False, "message": "Ruta inexistenta"})


def main():
    if not STATE_PATH.exists():
        reset_state()

    server = ThreadingHTTPServer(("0.0.0.0", 8765), Handler)
    print("Dashboard server running on http://localhost:8765")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        if is_agent_running():
            stop_agent()
        if is_autogpt_running():
            stop_autogpt_ui()
        server.server_close()


if __name__ == "__main__":
    main()
