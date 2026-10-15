# My approach: how I understood the problem and how I built it with Claude Code

Cloudworx asked candidates to explain how they used AI tools. This document is my account: how I read the problem, the approach I took, why the architecture fits the problem, how I used Claude Code to build it, and how I made sure I understand every part of it.

## Contents

- [1. How I understood the problem](#1-how-i-understood-the-problem)
- [2. My approach](#2-my-approach)
- [3. The architecture, and why it fits the problem](#3-the-architecture-and-why-it-fits-the-problem)
- [4. How I used Claude Code](#4-how-i-used-claude-code)
- [5. Where I pushed back](#5-where-i-pushed-back)
- [6. How I verified it](#6-how-i-verified-it)
- [7. Mistakes caught along the way](#7-mistakes-caught-along-the-way)
- [8. What I learned, and how I'd prompt next time](#8-what-i-learned-and-how-id-prompt-next-time)
- [Appendix: my prompts](#appendix-my-prompts)

## 1. How I understood the problem

On the surface the task is "show five enterprise tools in one sidebar". The hard part is that **none of those tools can be changed, and each one breaks the screen in a different way.** I listed the constraints that shape every decision:

| Constraint from the brief | Why it matters |
|---|---|
| The sidebar must show useful content within **1 s at p95** | The sizing tool takes **1–6 s**. So waiting for every system can never meet the target; the screen has to answer with what it has and fill in the rest later. |
| Sizing allows **20 requests/s**, pricing only **5 requests/s**, for the whole service | With **600 concurrent users**, calling the tools on every click would break those limits within seconds. Answers have to be reused, and calls rationed. |
| Systems are slow, fail, and have a **nightly maintenance window** | One failing tool must not blank the whole sidebar. Each panel needs its own status: fresh, old-but-useful, unavailable, or not allowed. |
| **Channel partners must never see prices**; documents have per-file permissions | Hiding prices in the UI is not enough: anyone can read the raw response. The server must never send them, and must not even fetch them. |
| A re-size changes the drive, its 3D model, the BOM and the price | All of these must stay consistent, and the item must keep one identity throughout. Two people editing at once must not silently overwrite each other. |
| An **AI assistant** answers questions and can trigger changes | It must never see more than the user can, it must not act on its own, and text inside catalogue or document data must not be able to instruct it. |
| A sixth tool (e.g. a harmonics calculator) may be added next week | Adding a system must not mean rewriting the others. |

My one-sentence summary of the problem: **build a fast, safe screen on top of slow, limited and fragile systems that I am not allowed to change.**

## 2. My approach

1. **Build the working service first, then write the design document.** That way the design document describes something that really exists and has been tested, not just a plan.
2. **Use Django, not the Node.js stack Cloudworx uses.** The brief explicitly invites a well-argued different stack. Python gives one language for the backend and the AI tooling, and the data (items, products, BOM lines) is relational. I then checked that the Django setup could meet the async needs of the sidebar (see [section 5](#5-where-i-pushed-back)).
3. **Use Claude Code as the builder, and stay the decision-maker.** I set the scope and the order of work, questioned the choices I wasn't sure about, and decided what was finished.
4. **Run everything myself.** The brief only asks for an API, but I asked for a browser test console so I could click through every flow by hand, not just trust the tests.
5. **Understand every part before submitting.** The brief says I must be able to *"explain and change any part"*. I spent about half my time on this: asking "why" until each answer made sense, and building an animated walkthrough of the workflow and the code for myself.

## 3. The architecture, and why it fits the problem

Claude Code proposed the first version of this design. I checked each piece against the constraints in [section 1](#1-how-i-understood-the-problem), questioned the parts I wasn't sure about, and kept it because each piece answers a specific constraint.

| Piece | The constraint it answers | How it works |
|---|---|---|
| **One backend-for-frontend (BFF)** | The screen needs one fast, coherent answer from five systems | The browser talks only to our server, which aggregates everything. A gateway alone can route requests, but it cannot merge systems or degrade one panel at a time. |
| **One adapter per system** | Each tool speaks a different "language", and a sixth tool must be easy to add | Each adapter only knows its own tool: URL, login, answer format. Adding a tool is one new file; `test_registry.py` proves the others don't change. |
| **A shared safety pipeline in the base adapter** | Slow tools, failures, rate limits | Every adapter inherits the same steps: permission → cache → single-flight → rate limit → circuit breaker → retries → deadline → fallback. A new adapter cannot forget any of them. |
| **A 0.9 s budget, then partial answers** | 1 s target vs 1–6 s sizing | The sidebar waits at most 0.9 s. Slow calls keep running in the background, fill the cache, and are pushed to the screen over server-sent events. |
| **A status per panel** (`ok`, `stale`, `unavailable`, `not_permitted`) | One failing tool must not blank the screen | The frontend renders each panel by its own status. Old data is shown with its timestamp instead of a spinner. |
| **Shared caches, with the permission check first** | 5 requests/s pricing with 600 users | Prices are cached per product + country + customer group, not per user. That's safe because the permission check runs before the cache is read. |
| **Token bucket that refuses instead of queueing** | Vendor rate limits | A call that can't get a slot before its deadline is refused at once and falls back to cached data, instead of waiting in a queue. |
| **Circuit breaker on sizing** | The tool fails and has a maintenance window | After 5 failures in a row, stop calling for 30 s, then test with one call. The panel shows the last saved result in the meantime. |
| **Permanent item id + revision number** | Re-size consistency, two people editing at once | A re-size changes the product and 3D model, never the id. Saves only succeed if the revision hasn't changed; otherwise the user gets a conflict instead of a silent overwrite. |
| **BOM calculated, never stored** | BOM and layout must agree | The BOM is computed from the items each time, so it cannot drift from them. |
| **AI tools that run as the user, and only propose writes** | The AI must not see more, or act alone | Tools reuse the same services and permission checks. `resize_item` only stores a proposal; the user's own click confirms it. Every call is audited. |

**What I deliberately left as a known gap:** the prototype doesn't yet check that a user belongs to the project they are reading. I listed it in the design document as the first thing a penetration test would find.

## 4. How I used Claude Code

### Tools

| Tool | What I used it for |
|---|---|
| **Claude Code** (desktop app, Claude Opus model) | Reading the brief, proposing the design, writing the code and tests, running commands, and explaining the code back to me |
| Gmail connector in Claude Code | Reading the assessment email and PDF directly from my inbox |
| Claude Code's built-in browser | Running the app and my learning material, and checking layouts with screenshots |
| Claude Docs | Writing the Part A design document and its editable architecture diagram, exported to PDF |
| Git + GitHub | Version control: [github.com/Sandeep2325/task_1](https://github.com/Sandeep2325/task_1) |

### How the work was split

| I did | Claude Code did |
|---|---|
| Read and framed the problem; chose Django; decided to build Part B first | Proposed the architecture and wrote the code, mocks, 49 tests, Dockerfile and CI |
| Checked the design against the constraints and questioned the parts I didn't understand | Explained its choices, and proved one with an experiment when I asked |
| Asked for a browser console so I could test by hand | Built the console and `dev.py` (one-command start) |
| Asked for a full API reference | Called every endpoint and wrote `docs/API.md` from the real responses |
| Tested every flow myself and decided when it was finished | Re-ran the test suite after every change and fixed its own failures |

### The workflow

1. **Understand the task.** *"Check my connected email: Cloudworx has given a task. Analyse it and start. Use Django."* Claude Code summarised the deliverables, the deadline (Oct 19), the weighting and the confidentiality rule, and started on Part B.
2. **Build the service.** Four Django apps (`integrations`, `workspace`, `assistant`, `mocks`); the three mock systems the brief asks for; all required endpoints plus all five optional items; 49 tests.
3. **Check what's left.** *"Everything done?"* That gave me a checklist: design document not started, nothing committed, Docker untested on my machine.
4. **Ship to GitHub.** Before the first commit we checked that no database, keys or PDF would be included, and set the commit author to me.
5. **Design document.** *"Now do the design document."* Written in Claude Docs, with the architecture diagram rendered and checked before export.
6. **Run it myself.** *"Run the server so I can run and test it. I need an interface as well."* That produced the browser console, which I used to click through every flow.
7. **Understand everything.** I asked for explanations at a beginner level, then for an animated walkthrough of both the workflow and the code, then for more depth on the Django structure, the database tables, the adapters and the APIs, until I could explain each part.
8. **Document the APIs.** *"I need proper docs for the workspace and assistant APIs."* The result is [docs/API.md](API.md), built from real captured responses.

## 5. Where I pushed back

These are the points where I didn't just accept the output.

| My question | Why I asked | What I found | Outcome |
|---|---|---|---|
| "Why not use Django REST Framework?" | DRF is the usual Django API tool, and I wanted to know what we were giving up | DRF views run synchronously. The sidebar needs async to call three slow systems at once and keep slow calls running after it answers. The cost is no auto-generated OpenAPI schema, which is why I asked for a written API reference. | Kept plain async Django; reasoning added to the design document |
| "Why uvicorn? Won't `python manage.py runserver` work?" | I wanted to use the standard Django command | `runserver` is WSGI: each request gets a throwaway event loop, so a slow call is cancelled instead of finishing in the background | **Proved by experiment:** the same test on both servers. Under `runserver` the second click still showed "loading"; under uvicorn it answered from the cache in 3 ms |
| "You are running on a different port, why?" | The app wasn't where I expected it | Another program was holding port 8000 | The port became configurable (`dev.py --port`), with the mock systems following it |
| "What is each model for?" then "I meant my database models" | I wanted to explain the actual tables, not the Python classes | Two real foreign keys (`project_id`, and the self-reference `parent_id`), a unique `(project_id, tag)` rule, and why the BOM, prices and catalogue are deliberately not tables | Explanation rebuilt from the real database schema |

## 6. How I verified it

- **49 automated tests** covering partial results when a system fails, price filtering by role, the re-size flow, the circuit breaker, retries, rate limiting, token checks and the AI confirmation flow.
- **End-to-end over real HTTP:** the running server, a real signed token, and every endpoint and error code (401, 404, 405, 409, 422). Those responses are the examples in [docs/API.md](API.md).
- **By hand in the browser console:**
  - re-sized DRV-03 from frame R7 to R8 and watched the BOM, the price (+€4,500) and the cabinet-fit warning update;
  - switched to a channel partner and confirmed no price appears anywhere;
  - switched the mock sizing tool to "down" and saw a partial sidebar, with the re-size refused.
- **Visual checks** of the diagram and the PDF pages, to catch overlapping or clipped text.

## 7. Mistakes caught along the way

| Problem | How it was caught | Fix |
|---|---|---|
| The pricing rate limiter allowed a burst of 5 on top of 5 per second, which could exceed the ERP's limit in a single second | While writing the rate-limit test | Burst lowered to 1; a test proves no one-second window exceeds the limit |
| "No drive variant fits" returned 503 (system down), when it is really a valid answer | Reviewing the re-size error cases | Returns 422 with a clear message |
| A test changed the mock's randomness in a way that also affected its latency | Test review | Replaced with a scripted fake, so the test is deterministic |
| The port was hard-coded, which broke on my machine | Running the server | `PORT` setting + `dev.py --port` |
| Diagram labels were crossed by connector lines | Render check | Labels moved and re-checked before export |

## 8. What I learned, and how I'd prompt next time

**What I learned about using an AI builder:** it is fast at producing a complete, tested system. The value I add is in framing the constraints, questioning the choices, verifying with real runs, and being able to explain and change the result. When I asked "why", the answers were usually good, and when one surprised me, an experiment settled it.

**How I'd prompt next time.** These are lessons learned, not prompts I used: start with the constraints and ask for options before any code.

> "The sidebar must answer within 1 s at p95, but sizing takes 1–6 s and pricing allows 5 requests/s for 600 users. Propose two backend designs, compare them against these constraints, and recommend one before writing code."

> "Channel partners must never see prices, including through the AI assistant. Show me where the permission check lives and write a test that proves a partner's response contains no price fields."

> "Explain each module as you build it, and give me a way to see it working in a browser."

**What I'd do differently:**
- Ask for explanations alongside each feature, not afterwards.
- Ask for the API reference earlier: writing it exercised every error case.
- Test the Docker image locally.

## Appendix: my prompts

My prompts in order, lightly edited for spelling. Short confirmations like "ok" are left out.

1. "Check my connected email: Cloudworx has given a task. Analyse it and start. Use Django." *(with the assessment PDF attached)*
2. "Everything done?"
3. *(gave the GitHub repository URL)* "This is the git repo."
4. "Why not use Django REST Framework?"
5. "Ok, now do the design document."
6. "All done?"
7. "Now can you run the server so I can run and test it? I need an interface as well."
8. "Ok, all done, right? Coding and documentation. Now I know nothing about this, so you need to explain what this is and why it was built. Think like I'm a fresher."
9. "Ok, commit and push the changes."
10. "Where is the architecture diagram?"
11. "What does A1 architecture mean?"
12. "Put this diagram in the PDF itself."
13. "Make an animation video so I can understand better. The animation needs to cover code understanding as well."
14. "You are running on a different port, why?"
15. "You need to include the Django structure and project as well, so I can understand better and explain them better."
16. "What is each model doing? What is the purpose of each, and how are they related to each other?"
17. "Why uvicorn? Won't `python manage.py runserver` work?"
18. "You still haven't explained what each model is for, where they are used, and their relations."
19. "I need this in explainer.html."
20. "When I asked for models, I meant my database models."
21. "Updated explainer.html?"
22. "Explain the adapter in detail in explainer.html."
23. "Now I need to run the project. What is the command to run uvicorn?"
24. "What actually happens in dev.py?"
25. "How do I run explainer.html?"
26. "I need proper docs for the APIs: the workspace APIs and the assistant APIs. Put them in explainer.html too, and split it into two parts: all the Django code in one tab and the workflow in another."
27. "Write a separate README on how I took this approach with Claude Code: the prompts, the ideas, and what I asked for."
28. "The document has to be from my point of view: how I understood the project and my approach."
