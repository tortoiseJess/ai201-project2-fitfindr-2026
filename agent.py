"""
The FitFindr planning loop.

This is the file that makes FitFindr an agent rather than a script. It decides
which tool to run next based on what the last one returned.

If your loop calls all three tools no matter what comes back, you have a list
of function calls. A loop looks at the last result before it picks the next
step. **That branch is the graded part of this unit.**

Build and test your three tools in `tools.py` first. Then come here.

    python agent.py          runs both example paths below
"""

import copy
import re

import config
import trace
from mcp_client import call_tool
from tools import suggest_outfit, create_fit_card
from generate import ModelUnavailable


# ── session state ─────────────────────────────────────────────────────────────

def new_session(query: str, wardrobe: dict) -> dict:
    """
    A fresh session for one user interaction.

    The session is the single source of truth for a run. Every tool result goes
    in here, and the next tool reads it back out.

    You could pass values straight from one call to the next. It would work,
    and you would not be able to test it — you can't print a variable you have
    already overwritten. Going through the session is what makes the state
    visible, and unit 4 has you write a criterion about exactly that.

    Add fields if you need them.
    """
    return {
        "query": query,              # what the user typed
        "parsed": {},                # description / size / max_price you pulled out of it
        "search_results": [],        # everything search_listings returned
        "selected_item": None,       # the one you chose — goes into suggest_outfit
        "wardrobe": wardrobe,        # the user's wardrobe
        "outfit_suggestion": None,   # what suggest_outfit returned
        "fit_card": None,            # what create_fit_card returned
        "error": None,               # set when the run ended early
    }


# ── planning loop ─────────────────────────────────────────────────────────────

def run_agent(query: str, wardrobe: dict) -> dict:
    """
    Run the loop once and return the finished session.

    Args:
        query:    what the user asked for, in plain language
                  (e.g. "vintage graphic tee under $30, size M").
        wardrobe: a wardrobe dict — get_example_wardrobe() or
                  get_empty_wardrobe() from utils/data_loader.py.

    Returns:
        The session dict. **Check session["error"] first** — if it isn't None,
        the run ended early and the later fields will still be None.

    ─────────────────────────────────────────────────────────────────────────
    TODO — build this, following the branch rule you wrote in Milestone 2.

      1. Start a session with new_session().

      2. Count the times round the loop, and call trace.check_iterations(count)
         on each one before you go again. It raises when the count passes
         MAX_ITERATIONS in config.py — see trace.py.

        3. Parse the query into a description, a size, and a max_price. Regex,
            string splitting, or asking the model are all fine — say which you
            chose in your README. Put the result in session["parsed"].

      4. Call search_listings() with what you parsed.
         Put the results in session["search_results"].

         ⚠️ THIS IS THE BRANCH. If nothing came back:
              - put a message in session["error"] saying what the user could
                change — "No results" is not that message
              - return the session
              - do NOT call suggest_outfit with nothing

      5. Choose an item — the first result is fine. Put it in
         session["selected_item"].

      6. Call suggest_outfit() with the selected item and the wardrobe.
         Put the result in session["outfit_suggestion"].

      7. Call create_fit_card() with the outfit and the item.
         Put the result in session["fit_card"].

      8. Return the session.

    ─────────────────────────────────────────────────────────────────────────
    IN UNIT 4 you come back and add two things:

      • Trace calls. One per step. `trace.step("search_listings", inputs=...,
        returned=...)` — see trace.py. Your README needs the output.

      • A handler for ModelUnavailable, so a bad key produces a message rather
        than a stack trace. The import is already at the top of this file.
    """

    trace.start_trace()



    # Step 1: start a session
    session = new_session(query, wardrobe)

    # Step 2: count the times round the loop; each pass runs one stage and
    # decides from its result which stage comes next
    count = 0
    stage = "parse"
    while stage != "done":
        count += 1
        trace.check_iterations(count)

        if stage == "parse":
            # Step 3: parse the query into description, size, and max_price
            # (regex — see the helper below)
            session["parsed"] = _parse_query(query)
            stage = "search"

        elif stage == "search":
            # Step 4: search with what was parsed and store everything returned
            parsed = session["parsed"]
            session["search_results"] = call_tool("search_listings", {
                "description": parsed["description"],
                "size": parsed["size"],
                "max_price": parsed["max_price"],
            })
            ceiling = parsed["max_price"]
            if ceiling is None:
                price_note = "criterion 5: parsed max_price = None (no ceiling found, price filter skipped)"
            elif not session["search_results"]:
                price_note = f"criterion 5: parsed max_price = ${ceiling:.2f}, no results to check"
            else:
                top_price = session["search_results"][0]["price"]
                highest = max(r["price"] for r in session["search_results"])
                relation = "<" if top_price < ceiling else ("=" if top_price == ceiling else ">")
                verdict = "PASS" if highest <= ceiling else "FAIL"
                price_note = (f"criterion 5: {verdict} — parsed max_price = ${ceiling:.2f}; "
                              f"top result ${top_price:.2f} ({relation} ceiling); "
                              f"highest result ${highest:.2f} ({'<=' if highest <= ceiling else '>'} ceiling)")
            trace.step("search_listings (via MCP)", inputs={"description": parsed["description"], "size": parsed["size"], "max_price": parsed["max_price"]},
                returned=session["search_results"],
                note=price_note + ("" if session["search_results"] else " | branch: no results, stopping before suggest_outfit"))
            # Criterion 3: frozen copy of the top result, the only item passed on
            top_snapshot = copy.deepcopy(session["search_results"][0]) if session["search_results"] else None
            # THE BRANCH: nothing came back, so stop before suggest_outfit and
            # tell the user what they could change
            if not session["search_results"]:
                session["error"] = _no_results_message(parsed)
                return session
            stage = "select"

        elif stage == "select":
            # Step 5: choose the item — the first (best-scoring) result
            session["selected_item"] = session["search_results"][0]
            stage = "outfit"

        elif stage == "outfit":
            # Step 6: suggest an outfit from the selected item and the wardrobe
            item_in = session["selected_item"]
            session["outfit_suggestion"] = suggest_outfit(
                item_in, session["wardrobe"]
            )
            outfit_snapshot = session["outfit_suggestion"]
            # Criterion 3, search -> outfit: the item sent on is exactly the
            # top search result, unchanged.
            ok = item_in == top_snapshot
            trace.step("suggest_outfit",
                inputs={"new_item": item_in, "wardrobe": session["wardrobe"]},
                returned=session["outfit_suggestion"],
                note=f"criterion 3 search→outfit: {'PASS' if ok else 'FAIL'} — "
                     f"sent '{item_in.get('title')}' (${item_in.get('price')}), "
                     f"search top was '{top_snapshot.get('title')}'")
            stage = "fit_card"

        elif stage == "fit_card":
            # Step 7: write the fit card from the outfit and the item
            item_fc = session["selected_item"]
            outfit_fc = session["outfit_suggestion"]
            session["fit_card"] = create_fit_card(outfit_fc, item_fc)
            # Criterion 3, outfit -> fit card: both inputs are what the earlier
            # steps produced, and create_fit_card didn't mutate the item.
            outfit_same = outfit_fc == outfit_snapshot
            item_same = item_fc == top_snapshot
            ok = outfit_same and item_same
            trace.step("create_fit_card",
                inputs={"outfit": outfit_fc, "new_item": item_fc},
                returned=session["fit_card"],
                note=f"criterion 3 outfit→fit_card: {'PASS' if ok else 'FAIL'} — "
                     f"outfit unchanged={outfit_same}, item==top result={item_same}; "
                     f"word count: {len(session['fit_card'].split())} (criterion 4: < 150)")
            stage = "done"

    # Step 8: return the finished session
    return session


def _parse_query(query: str) -> dict:
    """Pull description, size, and max_price out of a plain-language query."""
    text = query.strip()

    # max_price: "under $30", "below 25", "up to $40", or a bare "$30"
    max_price = None
    price_match = re.search(
        r"(?:under|below|less than|up to|max(?:imum)?|<=?)\s*\$?\s*(\d+(?:\.\d+)?)",
        text, re.IGNORECASE,
    ) or re.search(r"\$\s*(\d+(?:\.\d+)?)", text)
    if price_match:
        max_price = float(price_match.group(1))
        text = text.replace(price_match.group(0), " ")

    # size: "size M", "size US 9", "size 32"
    size = None
    size_match = re.search(
        r"\bsize\s*:?\s*((?:us\s*)?(?:[xs]{0,3}[sml]|\d+(?:\.\d+)?))\b",
        text, re.IGNORECASE,
    )
    if size_match:
        size = size_match.group(1).upper()
        text = text.replace(size_match.group(0), " ")

    # description: what's left, minus filler words
    filler = {"looking", "for", "a", "an", "the", "i", "want", "need", "find", "me", "some", "in", "please"}
    words = [w for w in re.findall(r"[A-Za-z0-9']+", text) if w.lower() not in filler]
    return {"description": " ".join(words), "size": size, "max_price": max_price}


def _no_results_message(parsed: dict) -> str:
    """Say what the user could change, based on which filters were applied."""
    tips = []
    if parsed["max_price"] is not None:
        tips.append(f"raise your ${parsed['max_price']:.0f} budget")
    if parsed["size"]:
        tips.append(f"drop the size {parsed['size']} filter")
    tips.append("try broader keywords than \"" + parsed["description"] + "\"")
    return "No listings matched. You could " + ", ".join(tips[:-1]) + (", or " if len(tips) > 1 else "") + tips[-1] + "."


# ── running it directly ───────────────────────────────────────────────────────

def _show(session: dict) -> None:
    if session["error"]:
        print(f"  stopped: {session['error']}")
        print(f"  fit_card is {session['fit_card']!r} — it should still be None here")
        return

    item = session["selected_item"] or {}
    print(f"  found:    {item.get('title')} — ${item.get('price')} on {item.get('platform')}")
    print(f"  outfit:   {session['outfit_suggestion']}")
    print(f"  fit card: {session['fit_card']}")


if __name__ == "__main__":
    from utils.data_loader import get_example_wardrobe

    print("=== A query the data can match ===")
    _show(run_agent(
        query="looking for a vintage graphic tee under $30",
        wardrobe=get_example_wardrobe(),
    ))

    print("\n=== A query it can't ===")
    _show(run_agent(
        query="designer ballgown size XXS under $5",
        wardrobe=get_example_wardrobe(),
    ))

    print(
        "\nThe second one should stop before the fit card. If both paths look "
        "the same,\nthe branch isn't doing anything yet."
    )
