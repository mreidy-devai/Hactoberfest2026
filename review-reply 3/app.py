"""Review Reply: draft replies to customer reviews with a model that runs on your own computer."""
import streamlit as st

import core
from core import BusinessProfile, OllamaClient, Review

st.set_page_config(page_title="Review Reply", page_icon="💬", layout="wide")

SS = st.session_state
SS.setdefault("reviews", [])
SS.setdefault("drafts", [])
SS.setdefault("themes", None)
SS.setdefault("gen", 0)  # bumped whenever drafts are regenerated so text boxes pick up the new text

# ------------------------------------------------------------------ sidebar
with st.sidebar:
    st.title("Review Reply")
    st.caption("Runs on this computer. Your reviews never leave it.")

    host = st.text_input("Ollama address", core.OLLAMA_URL)
    probe = OllamaClient(host=host)
    up = probe.is_up()
    models = probe.list_models() if up else []
    if up:
        st.success("Local model server is running")
    else:
        st.error("Can't reach Ollama. Open the Ollama app, then refresh.")
    model = st.selectbox(
        "Model",
        models or [core.DEFAULT_MODEL],
        index=models.index(core.DEFAULT_MODEL) if core.DEFAULT_MODEL in models else 0,
        help="Bigger models write better replies but run slower. 7B to 8B is a good start.",
    )

    st.divider()
    st.subheader("Your business")
    profile = BusinessProfile(
        name=st.text_input("Business name", placeholder="Nonna's Kitchen"),
        kind=st.text_input("What kind of business", placeholder="family Italian restaurant"),
        owner_name=st.text_input("Owner's first name", placeholder="Maria"),
        tone=st.text_input("Tone", "warm, direct, professional"),
        sign_off=st.text_input("Sign-off", placeholder="Maria and the Nonna's team"),
        policies=st.text_area(
            "Policies and contact info",
            placeholder="Invite unhappy customers to call 973-555-0101.\nNever promise refunds in public.",
            height=100,
        ),
        voice_samples=st.text_area(
            "Replies you've written before (optional)",
            placeholder="Paste 2 to 4 real replies. The model will match your style.",
            height=140,
        ),
    )

client = OllamaClient(host=host, model=model)

# --------------------------------------------------------------------- main
st.header("1. Add your reviews")
tab_csv, tab_paste = st.tabs(["Upload a CSV", "Paste reviews"])

with tab_csv:
    up_file = st.file_uploader("CSV with columns like rating, review, author", type=["csv"])
    if up_file is not None and st.button("Load CSV", key="load_csv"):
        try:
            SS.reviews = core.parse_csv(up_file.getvalue())
            SS.drafts, SS.themes = [], None
            SS.gen += 1
        except ValueError as e:
            st.error(str(e))

with tab_paste:
    raw = st.text_area(
        "One review per block, separated by a line containing ---",
        height=200,
        placeholder="Dana | 2\nWaited 50 minutes and the pasta was cold.\n---\nSam | 5\nBest lasagna in Essex County.",
    )
    if st.button("Load pasted reviews", key="load_paste"):
        SS.reviews = core.parse_pasted(raw)
        SS.drafts, SS.themes = [], None
        SS.gen += 1

if not SS.reviews:
    st.info("No reviews loaded yet. Try the sample file in the `samples` folder.")
    st.stop()

s = core.stats(SS.reviews)
c1, c2, c3 = st.columns(3)
c1.metric("Reviews loaded", s["count"])
c2.metric("Average rating", s["average"] if s["average"] else "n/a")
c3.metric("Need your eyes first", s["needs_owner"], help="Mentions of illness, legal threats, billing fraud, or discrimination")

st.header("2. Draft replies")
if st.button("Draft replies for all reviews", type="primary", disabled=not up):
    SS.drafts = []
    SS.gen += 1
    bar = st.progress(0.0, text="Starting...")
    for i, r in enumerate(SS.reviews, 1):
        SS.drafts.append(core.draft_reply(client, profile, r))
        bar.progress(i / len(SS.reviews), text=f"Drafted {i} of {len(SS.reviews)}")
    bar.empty()

for i, d in enumerate(SS.drafts):
    r = d.review
    stars = "★" * (r.rating or 0) + "☆" * (5 - (r.rating or 0)) if r.rating else "unrated"
    with st.container(border=True):
        st.markdown(f"**{r.author or 'Reviewer'}** · {stars} {('· ' + r.platform) if r.platform else ''}")
        st.write(r.text)
        if d.flags:
            st.warning("Read this one yourself before posting: " + ", ".join(d.flags))
        if d.error:
            st.error(d.error)
        else:
            d.reply = st.text_area("Draft reply (edit freely)", d.reply, key=f"reply_{SS.gen}_{i}_{d.rev}", height=130)
            if st.button("Redo this one", key=f"redo_{SS.gen}_{i}"):
                new = core.draft_reply(client, profile, r)
                new.rev = d.rev + 1
                SS.drafts[i] = new
                st.rerun()

if SS.drafts:
    st.download_button(
        "Download all drafts (CSV)",
        core.drafts_to_csv(SS.drafts),
        file_name="review_replies.csv",
        mime="text/csv",
    )

st.header("3. What are customers really saying?")
if st.button("Summarize themes", disabled=not up):
    with st.spinner("Reading all reviews..."):
        try:
            SS.themes = core.summarize_themes(client, SS.reviews)
        except core.LLMError as e:
            st.error(str(e))

if SS.themes:
    t = SS.themes
    if t.get("analyzed") and t["analyzed"] < t.get("total", 0):
        st.caption(f"Based on the first {t['analyzed']} of {t['total']} reviews.")
    if t.get("top_strength"):
        st.success("**Biggest strength:** " + t["top_strength"])
    if t.get("top_fix"):
        st.error("**Most valuable fix:** " + t["top_fix"])
    for th in t.get("themes", []):
        st.markdown(
            f"- **{th.get('theme', '')}** ({th.get('sentiment', '')}, ~{th.get('mentions', '?')} mentions)"
            + (f": “{th['evidence']}”" if th.get("evidence") else "")
        )
