# Why RAG, and why I built this

I started this project for a practical reason: some of the roles I'm interested in ask for RAG experience. But "I built a RAG app" is only worth something if you can explain *why* RAG exists, why the problem you picked actually needed it, and what you learned that surprised you. This doc is that explanation — written up from a Q&A session where I interrogated my own project, kept here because I found learning this way valuable and others might too.

The project itself: a question-answering service over the UNSW Handbook (COMP/SENG courses, 216 documents) that answers questions like *"I've done COMP1531 and COMP2521 — can I enrol in COMP3311 in T1?"* with a short, cited answer linking to the exact handbook section. The details of what got built are in the [README](../README.md) and [IMPLEMENTATION.md](../IMPLEMENTATION.md); this doc is about the *why*.

## Why RAG matters at all

A bare LLM fails at this task in three distinct ways:

1. **It doesn't know the data.** Models are trained up to a cutoff, on public data. The 2026 handbook is fresh, niche, and changes yearly. No model reliably knows COMP prerequisites — and worse, it won't say "I don't know", it will *confidently invent* a plausible-sounding prerequisite. For an enrolment decision, a confident wrong answer is worse than no answer.

2. **You can't just paste everything into the prompt.** Stuffing all 216 documents into every request is expensive, slow, and models measurably lose accuracy on facts buried in the middle of huge contexts. RAG retrieves only the handful of chunks relevant to *this* question and gives the model just those.

3. **A bare LLM answer is unverifiable.** RAG answers come with receipts. Every answer here carries inline `[n]` citations resolving to the exact handbook section, so a student can check the claim. That's the difference between "the model said so" and "the handbook says so, here's the link."

The analogy that stuck: RAG turns a closed-book exam into an **open-book exam**. The model still does the reasoning; the facts come from a library it's handed at answer time.

**Why not fine-tuning?** Fine-tuning teaches a model *style and skills*, not reliable facts. It's expensive, has to be redone every time the handbook updates, and still hallucinates. With RAG, updating knowledge is re-running one ingest command — knowledge lives in a database, not in model weights.

## The misconception worth fixing: RAG ≠ agentic AI

It's tempting to think RAG works because models became "agentic" and can now go look things up. That's wrong, and it's worth being precise about, because it shows you understand the mechanism:

**RAG requires no special capability from the model at all.** In this project's pipeline, the *application code* — not the model — embeds the question, runs the hybrid SQL query against Postgres, picks the top chunks, and builds a prompt that is literally: *"Here are some handbook excerpts: […]. Now answer this question, citing your sources."* The model just reads text and writes text — the only thing it ever did. The original RAG paper is from **2020**, pre-ChatGPT, pre-agents. Any model that accepts a prompt can do RAG.

Where "agentic" does enter is a real and useful distinction:

- **Pipeline RAG (this project):** the app *always* retrieves, once, before generation. The model has no say. Simple, fast, predictable — and easy to evaluate, which is why this repo can produce hit@k tables at all.
- **Agentic RAG:** the model is given retrieval as a *tool* and decides for itself whether to search, what query to use, and whether to search again if the first results are bad. The consumer-facing examples are the "Deep Research" modes in Gemini, ChatGPT, and Claude — same loop, pointed at the open web; enterprise agentic RAG points it at a private corpus instead.

Choosing pipeline RAG here was deliberate, not a limitation: the eval shows a single retrieval already surfaces the gold chunk in the top 5 **96%** of the time, so an agentic loop would add latency, cost, and nondeterminism to recover failures that barely exist. Agentic retrieval earns its keep on multi-hop questions and corpora where the first search often misses — neither of which describes this one.

## What makes a domain "RAG-shaped"

RAG isn't opposed to the model's general intelligence — it *combines* general reasoning with specific facts the model can't reliably hold. A domain wants RAG when:

1. **The facts are outside the model** — proprietary (a law firm's case files, a bank's internal policies), too fresh, or too niche to be memorized.
2. **Wrong answers are expensive** — hallucination is unacceptable, so answers must be verifiable. In law or compliance, an uncited claim is worthless.
3. **The facts change** — re-training on every change is absurd; re-ingesting a document is trivial.

Plus a fourth that regulated industries care about: **auditability**. RAG gives you a retrieval log showing where every answer came from — this repo writes one `query_logs` row per request, answered or refused. Law, finance, and biotech hit all four, but the biggest real-world category is unglamorous: **enterprise internal knowledge** — wikis, support docs, policies.

Enrolment questions are RAG-shaped in miniature: fresh yearly data, exact right answers sitting in a source of truth, and a real cost to being confidently wrong.

## The landscape: systems besides RAG

RAG is one point on a spectrum of ways to get knowledge into an LLM system. The alternatives aren't competitors — they're answers to different questions:

- **Long context / prompt stuffing.** If the corpus fits the context window, paste it all in — no retrieval. Costs the whole corpus on every request and degrades on mid-context facts. A variant, **cache-augmented generation**, loads a small stable corpus into a cached prompt prefix once. 216 documents sit near the boundary where this becomes arguable; retrieval means each query pays for ~5 chunks instead.
- **Fine-tuning / continued pretraining.** Right for *behaviour* — format, tone, domain fluency — wrong for facts. The mature answer is that they compose: fine-tune for *how* the model responds, RAG for *what* it knows.
- **Structured tool use.** If the knowledge is already structured, give the model a *query tool* (text-to-SQL, function calling against a live API) instead of retrieving prose. Relevant here: the ingestion pipeline already parses enrolment rules into rule types + course codes, so a v2 could answer "can I enrol?" by *querying* parsed rules — deterministic logic instead of reading comprehension.
- **Knowledge graphs / GraphRAG.** Parse documents into entities and relations, retrieve by traversing the graph. Wins on **multi-hop** questions ("what's the full prerequisite *chain* to reach COMP4920?") where no single chunk contains the whole answer. Prerequisites are literally a graph, so this is the other natural v2.
- **Memory systems.** RAG over the model's own accumulated notes about a user — solves personalization and continuity rather than domain knowledge.
- **Agentic search.** Covered above: the model drives retrieval as a tool.

The decision heuristic: *stable behaviour?* fine-tune. *Structured?* query tool. *Relational/multi-hop?* graph. *Small and stable?* long context may suffice. *Large or changing document corpus needing cited answers?* RAG. Production systems mix them.

## The interesting findings

**The honest one: my hybrid retriever lost to plain full-text search.** The eval (34 hand-verified questions) showed FTS-only hits the gold chunk in the top 3 **89%** of the time vs **82%** for the vector+FTS hybrid — and I can explain why: every chunk is prefixed with its course code and title, so course names are verbatim lexical tokens and lexical search rarely needs semantic help. This corpus is a near-best case for lexical search. I reported that straight instead of re-tuning until hybrid "won" — the numbers are the point, not the architecture. Hybrid's measured value is *robustness* (it never collapses the way vector-only does, 64% hit@3) and *semantic recovery* on genuine paraphrases; on a messier corpus without code+title prefixing, the gap would move in fusion's favour.

**Knowing when not to answer matters as much as answering.** The refusal threshold (top fused score gates generation) was tuned by a sweep against the golden set — serving value 0.030, keeping all 28 answerable questions while refusing clean off-corpus ones. Trustworthy RAG fails safe instead of hallucinating.

**Groundedness is checked, not assumed.** An LLM-as-judge verifies each answer against only its retrieved sources: 93% of answers fully grounded, 96% carrying inline citations.

**The economics are absurd in a good way.** Embeddings run locally (`bge-small-en-v1.5`, free, private), generation is a cheap cloud call, every request logs its latency split, token counts, and dollar cost — and a full judged eval run costs about **$0.011**.

## The one-paragraph version

> LLMs hallucinate on facts outside their training data, and enrolment rules are exactly that — fresh, niche, and high-stakes. RAG grounds the model in retrieved handbook sections so every answer is cited and verifiable, and the service refuses when the corpus can't support an answer. I built the whole lifecycle — scraping, structure-aware chunking, hybrid vector+lexical retrieval in Postgres, streamed cited generation, and a golden-set evaluation. The eval showed my hybrid retriever *didn't* beat well-tuned full-text search on this corpus, and I can explain why — which taught me that retrieval strategy is corpus-dependent and that you have to measure rather than assume.

Anyone can say "I built RAG." The part that's worth something is: *I evaluated it, here's the number, and here's the surprising thing the number taught me.*
