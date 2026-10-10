"""
DOCUMENT TYPES - Structure definitions and structure checking for every
academic document type the programme sets.

Two jobs:
  1. setup_exam.py asks for a skeleton and a checklist for a given type.
  2. run_pipeline.py asks whether a draft actually has the sections that type
     requires, before the document is built.

An essay and a report are not interchangeable. An essay argues a thesis in
continuous prose with no numbered sections; a report investigates, presents
data in numbered sections with tables or figures, and ends in
recommendations. Marking criteria follow the form, so the harness checks the
form.

Section matching is by meaning, not by exact wording: "Method", "Methods",
"Methodology" and "Method of enquiry" all satisfy the same requirement, and
a numbered heading such as "## 3. Method" matches too.

Types (with the aliases each accepts):
    essay
    report
    research-paper        (journal-article)
    literature-review     (lit-review)
    dissertation          (thesis)
    case-study
    lab-report            (practical-report)
    research-proposal     (proposal)
    annotated-bibliography
    conference-paper
    conference-abstract   (abstract)
    book-review           (critique)
    poster                (academic-poster, conference-poster)   -> .pptx
    presentation          (slides, slide-deck, viva)             -> .pptx

  Added for health and social care programmes:
    reflective-essay           (reflection, reflective-account)
    critical-review            (critique, critical-appraisal, article-critique)
    policy-brief               (briefing-paper)
    policy-analysis            (policy-review, policy-critique)
    service-evaluation         (quality-improvement, qi-report, audit-report)
    systematic-review          (scoping-review)
    capstone-project           (capstone, final-project)
    reflective-portfolio       (portfolio, work-based-learning)
    personal-development-plan  (pdp, career-plan)
    health-promotion-plan      (health-promotion, health-campaign)
    ethical-dilemma-analysis   (ethical-analysis, ethical-dilemma)
    change-management-proposal (change-proposal, business-case)
    debate-position-paper      (debate, position-paper)

Beyond sections, a type can expect named frameworks (a reflective essay
should name its reflective model, a service evaluation its QI method), cap a
section's share of the words (description in a reflective essay), expect the
first person, or require a minimum number of entries (a portfolio).

Most types are delivered as a Word document. The poster and presentation
types are delivered as PowerPoint, which run_pipeline.py selects by reading
each type's 'output' field.

Usage:
    python document_types.py                     list the types
    python document_types.py essay               show one type's structure
    python document_types.py report <draft.md>   check a draft against a type
"""
import os
import re
import sys

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass


# --------------------------------------------------------------------------
# Section vocabulary: one requirement, many acceptable headings
# --------------------------------------------------------------------------

SECTION_SYNONYMS = {
    "abstract": [r"abstract", r"summary", r"executive\s+summary", r"synopsis"],
    "introduction": [r"introduction", r"introductory", r"background\s+and\s+aims"],
    "background": [r"background", r"context", r"literature\s+context",
                   r"rationale", r"background\s+and\s+rationale",
                   r"case\s+(?:presentation|background|details)",
                   r"terms\s+of\s+reference"],
    "literature_review": [r"literature\s+review", r"review\s+of\s+(?:the\s+)?literature",
                          r"theoretical\s+framework", r"conceptual\s+framework"],
    "aim": [r"aims?", r"aims?\s+and\s+objectives", r"objectives?",
            r"research\s+question", r"hypothesis", r"aim\s+and\s+hypothesis",
            r"purpose"],
    "method": [r"methods?", r"methodology", r"method\s+of\s+enquiry",
               r"materials?\s+and\s+methods?", r"approach", r"procedure",
               r"search\s+strategy", r"design"],
    "results": [r"results?", r"findings?", r"data", r"analysis\s+of\s+(?:the\s+)?(?:data|findings)"],
    "discussion": [r"discussion", r"analysis", r"critical\s+(?:analysis|discussion|evaluation)",
                   r"interpretation", r"themes?", r"thematic\s+(?:analysis|synthesis|discussion)",
                   r"synthesis", r"evaluation", r"^so\s+what",
                   r"abstract\s+conceptuali[sz]ation"],
    "conclusion": [r"conclusions?", r"concluding\s+(?:remarks|comments)"],
    "recommendations": [r"recommendations?", r"implications?\s+for\s+practice",
                        r"recommendations?\s+and\s+implications?"],
    # In an annotated bibliography the reference list IS the "annotated
    # entries" section, so that heading satisfies the requirement.
    "references": [r"(?<!of )references?", r"bibliography", r"reference\s+list",
                   r"works\s+cited", r"annotated\s+entries", r"entries",
                   r"annotated\s+bibliography"],
    "timeline": [r"timeline", r"timetable", r"schedule", r"work\s+plan",
                 r"project\s+plan", r"gantt"],
    "ethics": [r"ethics", r"ethical\s+(?:considerations?|approval|issues)",
               r"research\s+ethics"],
    "limitations": [r"limitations?", r"strengths?\s+and\s+limitations?"],
    "confidentiality": [r"confidentiality", r"anonymity",
                        r"confidentiality\s+(?:statement|and\s+consent)",
                        r"consent\s+and\s+confidentiality", r"pseudonym"],
    # Care plans, named as his Module 4 rubric names them.
    "service_user": [r"service\s+user\s+statement",
                     r"about\s+the\s+(?:service\s+user|person)",
                     r"service\s+user\s+(?:profile|details|background)",
                     r"person(?:al)?\s+(?:profile|details)"],
    "needs": [r"needs?", r"examples?\s+of\s+needs?", r"needs?\s+assessment",
              r"identified\s+needs?", r"assessment\s+of\s+needs?"],
    "goals": [r"goals?", r"smart\s+goals?", r"key\s+features",
              r"goals?\s+and\s+interventions?", r"interventions?",
              r"planned\s+(?:care|support)"],
    "mdt": [r"multi[\s-]?disciplinary(?:\s+team)?", r"\bmdt\b",
            r"role\s+of\s+the\s+(?:health\s+and\s+social\s+care\s+)?professional",
            r"team\s+(?:roles?|contributions?)", r"working\s+with\s+others"],
    "capacity": [r"capacity", r"consent", r"capacity\s+and\s+consent",
                 r"mental\s+capacity", r"best\s+interests?"],
    "safeguarding": [r"safeguarding", r"risk\s+(?:assessment|management)",
                     r"protection", r"safety"],
    "review": [r"review", r"review\s+(?:and\s+evaluation|date|arrangements)",
               r"monitoring", r"evaluation\s+and\s+review"],
    # Rolfe's What? So what? Now what?, which his journals ask for by name.
    "reflection_what": [r"what\??$", r"what\s+happened"],
    "reflection_so_what": [r"so\s+what\??", r"why\s+(?:it|this)\s+matters"],
    "reflection_now_what": [r"now\s+what\??", r"next\s+steps?",
                            r"what\s+i\s+will\s+do"],
    "appendices": [r"appendix", r"appendices", r"appendix\s+[a-z0-9]+"],
    "acknowledgements": [r"acknowledgements?", r"acknowledgments?"],
    "contents": [r"contents", r"table\s+of\s+contents"],
    "keywords": [r"key\s?words?"],
    "summary_of_book": [r"summary\s+of\s+(?:the\s+)?(?:book|argument|text)",
                        r"overview\s+of\s+(?:the\s+)?(?:book|text)",
                        r"synopsis\s+of\s+(?:the\s+)?book"],

    # --- reflective writing: Gibbs, Driscoll (What? So what? Now what?),
    #     Kolb and Rolfe. A pattern starting with "^" matches only at the
    #     start of a heading, so "So what?" is not mistaken for "What?".
    "description": [r"description", r"^what\b(?!\s+next)", r"what\s+happened",
                    r"the\s+(?:event|experience|incident|situation)",
                    r"concrete\s+experience"],
    "feelings": [r"feelings?", r"emotions?", r"reflective\s+observation"],
    "action_plan": [r"action\s+plan", r"^now\s+what", r"active\s+experimentation",
                    r"next\s+steps", r"development\s+plan",
                    r"future\s+(?:practice|learning|development)"],
    "reflective_entries": [r"(?:reflective\s+)?entry", r"reflection\s+\d+",
                           r"reflective\s+account", r"week\s+\d+",
                           r"critical\s+incident", r"placement\s+(?:reflection|log)"],

    # --- appraisal and policy ---
    "summary_of_article": [r"summary\s+of\s+(?:the\s+)?(?:article|paper|study|research)",
                           r"overview\s+of\s+(?:the\s+)?(?:article|paper|study)",
                           r"(?:article|paper|study)\s+summary"],
    "appraisal": [r"critical\s+appraisal", r"appraisal", r"critique",
                  r"strengths\s+and\s+(?:weaknesses|limitations)",
                  r"validity", r"critical\s+evaluation"],
    "policy_options": [r"(?:policy\s+)?options", r"alternatives?",
                       r"options?\s+appraisal", r"approaches\s+considered"],

    # --- planning and improvement ---
    "needs_assessment": [r"needs?\s+assessment", r"health\s+needs",
                         r"target\s+(?:group|population)", r"population\s+profile",
                         r"epidemiolog\w*"],
    "implementation": [r"implementation", r"delivery\s+plan",
                       r"intervention\s+(?:plan|design)"],
    "evaluation_plan": [r"evaluation", r"evaluation\s+plan",
                        r"monitoring(?:\s+and\s+evaluation)?",
                        r"measuring\s+(?:success|impact|outcomes)"],
    "self_assessment": [r"self[\s-]+assessment", r"swot", r"skills\s+audit",
                        r"personal\s+(?:audit|profile)", r"current\s+position"],
    "goals": [r"goals?", r"smart\s+(?:goals|objectives|targets)", r"targets?"],
    "change_model": [r"change\s+model", r"model\s+of\s+change",
                     r"change\s+(?:theory|framework)", r"managing\s+(?:the\s+)?change"],
    "stakeholders": [r"stakeholders?", r"stakeholder\s+analysis"],

    # --- ethics and debate ---
    "dilemma": [r"(?:the\s+)?(?:ethical\s+)?dilemma", r"the\s+(?:case|scenario)",
                r"case\s+scenario"],
    "legal_framework": [r"legal", r"legislation", r"law",
                        r"professional\s+(?:framework|standards|codes?)",
                        r"codes?\s+of\s+(?:conduct|practice)"],
    "arguments_for": [r"arguments?\s+(?:for|in\s+favour|supporting)",
                      r"the\s+case\s+for", r"supporting\s+arguments?",
                      r"^arguments?\b", r"^main\s+arguments?"],
    "counter_arguments": [r"counter[\s-]*arguments?", r"rebuttals?",
                          r"arguments?\s+against", r"the\s+case\s+against",
                          r"opposing\s+(?:views|arguments?)", r"objections?"],
}

SECTION_LABELS = {
    "abstract": "Abstract / Executive summary",
    "introduction": "Introduction",
    "background": "Background / Context",
    "literature_review": "Literature review",
    "aim": "Aim, objectives or hypothesis",
    "method": "Method / Methodology",
    "results": "Results / Findings",
    "discussion": "Discussion / Analysis",
    "conclusion": "Conclusion",
    "recommendations": "Recommendations",
    "references": "References",
    "timeline": "Timeline",
    "ethics": "Ethical considerations",
    "limitations": "Limitations",
    "confidentiality": "Confidentiality / anonymity statement",
    "service_user": "Service User Statement",
    "needs": "Needs (physical, emotional, social)",
    "goals": "SMART goals and interventions",
    "mdt": "The multidisciplinary team and the professional's role",
    "capacity": "Capacity and consent",
    "safeguarding": "Safeguarding and risk",
    "review": "Review and evaluation",
    "reflection_what": "What? (what happened)",
    "reflection_so_what": "So what? (what it means)",
    "reflection_now_what": "Now what? (what you will do)",
    "appendices": "Appendices",
    "acknowledgements": "Acknowledgements",
    "contents": "Table of contents",
    "keywords": "Keywords",
    "summary_of_book": "Summary of the book",
    "description": "Description of the experience",
    "feelings": "Feelings",
    "action_plan": "Action plan / next steps",
    "reflective_entries": "Reflective entries",
    "summary_of_article": "Summary of the article",
    "appraisal": "Critical appraisal",
    "policy_options": "Policy options",
    "needs_assessment": "Needs assessment",
    "implementation": "Implementation",
    "evaluation_plan": "Evaluation plan",
    "self_assessment": "Self-assessment (SWOT or skills audit)",
    "goals": "Development goals",
    "change_model": "Change model",
    "stakeholders": "Stakeholder analysis",
    "dilemma": "The dilemma",
    "legal_framework": "Legal and professional framework",
    "arguments_for": "Arguments for",
    "counter_arguments": "Counter-arguments and rebuttal",
}


# --------------------------------------------------------------------------
# Terms a type is expected to use. A reflective essay that names no
# reflective model, or a service evaluation that never mentions PDSA, audit
# or SQUIRE, is missing something a marker will look for. Absence warns.
# --------------------------------------------------------------------------

REFLECTIVE_MODELS = ("a named reflective model (Gibbs, Driscoll, Rolfe, Kolb...)",
                     [r"gibbs", r"driscoll", r"rolfe", r"kolb", r"sch[oö]n",
                      r"\bjohns\b", r"borton", r"atkins\s+and\s+murphy",
                      r"brookfield"])
APPRAISAL_TOOLS = ("an appraisal framework (CASP, JBI, a hierarchy of evidence...)",
                   [r"\bcasp\b", r"critical\s+appraisal\s+skills", r"\bjbi\b",
                    r"joanna\s+briggs", r"\bcraap\b", r"caldwell", r"\bamstar\b",
                    r"\bcoreq\b", r"\bstrobe\b", r"\bconsort\b",
                    r"hierarchy\s+of\s+evidence", r"levels?\s+of\s+evidence"])
POLICY_FRAMEWORKS = ("a policy analysis framework (Walt and Gilson, Kingdon...)",
                     [r"policy\s+triangle", r"walt\s+and\s+gilson", r"kingdon",
                      r"multiple\s+streams", r"policy\s+cycle",
                      r"advocacy\s+coalition", r"punctuated\s+equilibrium",
                      r"bardach", r"stages?\s+heuristic"])
QI_FRAMEWORKS = ("a quality improvement framework (PDSA, SQUIRE, audit cycle...)",
                 [r"\bpdsa\b", r"plan[\s-]+do[\s-]+study[\s-]+act",
                  r"model\s+for\s+improvement", r"\bsquire\b", r"audit\s+cycle",
                  r"clinical\s+audit", r"lean\s+(?:methodology|thinking|approach)",
                  r"six\s+sigma", r"donabedian", r"run\s+charts?",
                  r"statistical\s+process\s+control"])
GOVERNANCE = ("its governance status - service evaluation, audit or research",
              [r"service\s+evaluation", r"\baudit\b", r"research\s+ethics",
               r"ethical\s+approval", r"governance",
               r"health\s+research\s+authority", r"\bhra\b"])
REVIEW_STANDARDS = ("a reporting standard such as PRISMA",
                    [r"\bprisma\b", r"\bmoose\b", r"\bentreq\b"])
DATABASES = ("the databases searched (CINAHL, MEDLINE, PubMed...)",
             [r"cinahl", r"medline", r"pubmed", r"psycinfo", r"embase",
              r"cochrane", r"scopus", r"web\s+of\s+science", r"\bassia\b",
              r"social\s+care\s+online", r"google\s+scholar"])
SEARCH_FRAMEWORKS = ("a search framework (PICO, PEO, SPIDER...)",
                     [r"\bpico\b", r"\bpicos\b", r"\bpeo\b", r"\bspider\b",
                      r"\bpcc\b"])
SMART = ("SMART goals or objectives", [r"\bsmart\b",
                                       r"specific,?\s+measurable"])
SWOT = ("a SWOT analysis or skills audit", [r"\bswot\b", r"skills\s+audit",
                                            r"\bsoar\b", r"self[\s-]+assessment"])
HEALTH_PROMOTION_MODELS = ("a health promotion model or behaviour change theory",
                           [r"tannahill", r"beattie", r"\btones\b",
                            r"health\s+belief\s+model", r"transtheoretical",
                            r"stages?\s+of\s+change", r"prochaska",
                            r"\bcom-?b\b", r"behaviour\s+change\s+wheel",
                            r"planned\s+behaviou?r", r"social\s+cognitive",
                            r"ottawa\s+charter", r"dahlgren",
                            r"naidoo\s+and\s+wills", r"precede"])
ETHICAL_FRAMEWORKS = ("an ethical framework (Beauchamp and Childress, "
                      "deontology, utilitarianism...)",
                      [r"beauchamp", r"childress", r"autonomy", r"beneficence",
                       r"non-?maleficence", r"deontolog", r"utilitarian",
                       r"virtue\s+ethics", r"consequential",
                       r"four\s+principles", r"seedhouse", r"ethical\s+grid"])
LEGAL_FRAMEWORKS = ("the relevant law or professional code",
                    [r"mental\s+capacity\s+act", r"care\s+act",
                     r"human\s+rights\s+act", r"equality\s+act",
                     r"children\s+act", r"data\s+protection", r"\bgdpr\b",
                     r"codes?\s+of\s+(?:conduct|practice)", r"\bnmc\b",
                     r"\bhcpc\b", r"social\s+work\s+england", r"caldicott",
                     r"duty\s+of\s+care", r"safeguarding"])
PERSON_CENTRED = ("what matters to the person: preferences, routines, "
                  "culture or faith",
                  [r"what\s+matters", r"person[\s-]?centred", r"preferences?",
                   r"wishes", r"routines?", r"cultur", r"faith", r"religio",
                   r"strengths?[\s-]based", r"choices?"])
CARE_PLAN_PARTS = ("the parts of a care plan: needs, goals, interventions "
                   "and a review",
                   [r"needs?\s+assessment", r"interventions?", r"review\s+date",
                    r"outcomes?", r"goals?"])
SEARCH_MECHANICS = ("the mechanics of the search: keywords, Boolean "
                    "operators, inclusion and exclusion criteria",
                    [r"boolean", r"truncat", r"wildcard", r"inclusion\s+criteria",
                     r"exclusion\s+criteria", r"keywords?", r"search\s+terms?",
                     r"filters?"])
PLAIN_LANGUAGE = ("plain language written to the reader ('you can...'), not "
                  "academic prose",
                  [r"\byou\b", r"\byour\b", r"ask\s+your", r"talk\s+to",
                   r"find\s+out", r"call\s+", r"visit\s+"])
CHANGE_MODELS = ("a change management model (Lewin, Kotter, ADKAR...)",
                 [r"lewin", r"kotter", r"\badkar\b", r"mckinsey", r"\bpdsa\b",
                  r"\bbridges\b", r"diffusion\s+of\s+innovation",
                  r"force[\s-]+field", r"lippitt"])


# --------------------------------------------------------------------------
# The types
#
#   required     - absent means FAIL
#   recommended  - absent means WARN
#   numbered     - True expects "## 1. Heading", False expects it NOT numbered
#   visuals      - True expects at least one table or figure
#   prose        - True expects continuous prose (heavy bullet use warns)
#   skeleton     - the section headings written into 02_Drafts/skeleton.md
#   checklist    - type-specific items added to EXAM_CONFIG.md
# --------------------------------------------------------------------------

DOCUMENT_TYPES = {
    "essay": {
        "label": "Essay",
        "aliases": [],
        "purpose": ("Argument-driven. Develops and defends a thesis in "
                    "continuous prose, with no numbered sections."),
        "required": ["introduction", "conclusion", "references"],
        "recommended": [],
        "numbered": False,
        "visuals": False,
        "prose": True,
        "typical_words": 2500,
        "skeleton": ["Introduction", "Main Body", "Conclusion", "References"],
        "checklist": [
            "Thesis stated explicitly in the introduction",
            "Each paragraph advances the argument, not just the topic",
            "Continuous prose throughout - no bullet lists in the body",
            "No numbered section headings (that is a report, not an essay)",
            "Conclusion answers the question rather than summarising",
        ],
    },
    "report": {
        "label": "Report",
        "aliases": [],
        "purpose": ("Information-driven. Investigates, presents evidence in "
                    "numbered sections with visual aids, and recommends "
                    "action."),
        "required": ["introduction", "results", "conclusion",
                     "recommendations", "references"],
        "recommended": ["abstract", "background", "method", "appendices"],
        "numbered": True,
        "visuals": True,
        "prose": False,
        "contents_page": True,
        "typical_words": 2500,
        "skeleton": ["1. Introduction", "2. Terms of Reference", "3. Method",
                     "4. Findings", "5. Discussion", "6. Conclusion",
                     "7. Recommendations", "References", "Appendices"],
        "checklist": [
            "Sections numbered consecutively",
            "At least one table or figure, captioned and referred to in the text",
            "Findings kept separate from interpretation",
            "Recommendations follow from the findings and are actionable",
            "Executive summary written last, covering the whole report",
        ],
    },
    "research-paper": {
        "label": "Research paper / journal article",
        "aliases": ["journal-article", "research-article"],
        "purpose": ("Original research reported in IMRaD form: Introduction, "
                    "Method, Results and Discussion."),
        "required": ["abstract", "introduction", "method", "results",
                     "discussion", "references"],
        "recommended": ["keywords", "limitations", "conclusion", "ethics"],
        "numbered": None,
        "visuals": True,
        "prose": False,
        "typical_words": 5000,
        "skeleton": ["Abstract", "Keywords", "Introduction", "Method",
                     "Results", "Discussion", "Limitations", "Conclusion",
                     "References"],
        "checklist": [
            "Abstract is structured and within the journal's word limit",
            "Method is reproducible from the description given",
            "Results reported without interpretation",
            "Discussion addresses the limitations honestly",
            "Ethical approval stated where human participants are involved",
        ],
    },
    "literature-review": {
        "label": "Literature review",
        "aliases": ["lit-review", "critical-literature-review",
                    "critical-lit-review", "narrative-review"],
        "purpose": ("Systematic synthesis and critical evaluation of existing "
                    "published research, organised by theme."),
        "required": ["introduction", "method", "discussion", "conclusion",
                     "references"],
        "recommended": ["limitations"],
        "numbered": None,
        "visuals": True,
        "prose": False,
        "typical_words": 3000,
        "skeleton": ["Introduction", "Search Strategy", "Inclusion and "
                     "Exclusion Criteria", "Thematic Synthesis",
                     "Discussion", "Conclusion", "References"],
        "checklist": [
            "Search strategy stated: databases, terms, date range",
            "Inclusion and exclusion criteria explicit",
            "Organised by THEME, never author by author",
            "Each theme weighs agreement against disagreement",
            "Gap in the literature identified",
        ],
    },
    "dissertation": {
        "label": "Dissertation / thesis",
        "aliases": ["thesis"],
        "purpose": ("Substantial independent research project submitted for "
                    "degree completion."),
        "required": ["abstract", "introduction", "literature_review",
                     "method", "results", "discussion", "conclusion",
                     "references"],
        "recommended": ["acknowledgements", "contents", "ethics",
                        "limitations", "appendices"],
        "numbered": True,
        "visuals": True,
        "prose": False,
        "typical_words": 10000,
        "skeleton": ["Abstract", "Acknowledgements", "Table of Contents",
                     "1. Introduction", "2. Literature Review",
                     "3. Methodology", "4. Findings", "5. Discussion",
                     "6. Conclusion and Recommendations", "References",
                     "Appendices"],
        "checklist": [
            "Ethical approval obtained and documented before data collection",
            "Chapters numbered, with a contents page matching them",
            "Abstract written last and standing alone",
            "Word count confirmed against the programme handbook",
            "Every appendix referred to from the main text",
        ],
    },
    "case-study": {
        "label": "Case study",
        "aliases": ["case-study-analysis", "case-analysis"],
        "purpose": ("In-depth examination of a real case, applying "
                    "theoretical frameworks to practice."),
        "required": ["introduction", "background", "discussion",
                     "conclusion", "references"],
        "recommended": ["confidentiality", "recommendations", "limitations"],
        "numbered": None,
        "visuals": False,
        "prose": False,
        "contents_page": True,
        "typical_words": 2500,
        "skeleton": ["Introduction", "Confidentiality Statement",
                     "Case Background", "Analysis and Discussion",
                     "Implications for Practice", "Conclusion", "References"],
        "checklist": [
            "Confidentiality statement present: pseudonym used, consent noted",
            "NO identifying details of the service user, staff or setting",
            "Theory applied to the case, not merely described alongside it",
            "Practice implications drawn out explicitly",
            "Reflection distinguishes observation from interpretation",
        ],
    },
    "lab-report": {
        "label": "Lab report",
        "aliases": ["practical-report", "laboratory-report"],
        "purpose": ("Concise documentation of an experiment: hypothesis, "
                    "method, results and quantitative analysis."),
        "required": ["aim", "method", "results", "discussion", "references"],
        "recommended": ["abstract", "conclusion", "limitations"],
        "numbered": True,
        "visuals": True,
        "prose": False,
        "typical_words": 1500,
        "skeleton": ["1. Aim and Hypothesis", "2. Method", "3. Results",
                     "4. Discussion", "5. Conclusion", "References"],
        "checklist": [
            "Hypothesis stated in testable terms",
            "Method written so another student could repeat it",
            "Results presented in a table or figure, with units",
            "Sources of error identified in the discussion",
            "Raw data included in an appendix if required",
        ],
    },
    "research-proposal": {
        "label": "Research proposal",
        "aliases": ["proposal"],
        "purpose": ("Formal outline of a planned study: background, "
                    "methodology, timeline and justification."),
        # A proposal opens with either heading, so either satisfies it.
        "required": [("introduction", "background"), "aim", "method",
                     "timeline", "references"],
        "recommended": ["ethics", "limitations", "literature_review"],
        "numbered": True,
        "visuals": False,
        "prose": False,
        "typical_words": 2000,
        "skeleton": ["1. Background and Rationale",
                     "2. Aims and Objectives", "3. Literature Context",
                     "4. Methodology", "5. Ethical Considerations",
                     "6. Timeline", "7. Anticipated Limitations",
                     "References"],
        "checklist": [
            "Research question is answerable within the time available",
            "Methodology justified, not merely named",
            "Ethical issues addressed, including consent and data storage",
            "Timeline realistic and broken into stages",
            "Significance of the study stated",
        ],
    },
    "annotated-bibliography": {
        "label": "Annotated bibliography",
        "aliases": ["annotated-biblio"],
        "purpose": ("Alphabetised source list, each entry followed by an "
                    "evaluative annotation."),
        "required": ["references"],
        "recommended": ["introduction"],
        "numbered": False,
        "visuals": False,
        "prose": False,
        "typical_words": 2000,
        "annotations_required": True,
        "skeleton": ["Introduction", "Annotated Entries"],
        # The entries ARE the reference list here, so they carry the words.
        "guide": {
            "Introduction": (10, "The topic and the purpose of the "
                             "bibliography, and how the sources were chosen."),
            "Annotated Entries": (90, "Each entry: the full "
                                  "reference, then a paragraph that "
                                  "summarises the source, evaluates its "
                                  "reliability and method, and says how it "
                                  "is relevant to your topic. Alphabetical "
                                  "order."),
        },
        "checklist": [
            "Every entry followed by its own annotation",
            "Entries in alphabetical order",
            "Each annotation evaluates relevance and validity, not just content",
            "Annotations of consistent length",
            "Full reference (in your style) above each annotation",
        ],
    },
    "conference-paper": {
        "label": "Conference paper",
        "aliases": [],
        "purpose": ("Condensed manuscript prepared for an academic "
                    "symposium or proceedings."),
        "required": ["abstract", "introduction", "method", "discussion",
                     "conclusion", "references"],
        "recommended": ["keywords", "results", "limitations"],
        "numbered": None,
        "visuals": True,
        "prose": False,
        "typical_words": 3000,
        "skeleton": ["Abstract", "Keywords", "Introduction", "Approach",
                     "Findings", "Discussion", "Conclusion", "References"],
        "checklist": [
            "Within the call for papers' word or page limit",
            "Abstract works as a standalone submission",
            "Formatted to the conference template",
            "Contribution stated in the introduction",
        ],
    },
    "conference-abstract": {
        "label": "Conference abstract",
        "aliases": ["abstract"],
        "purpose": ("Structured summary submitted for consideration at a "
                    "symposium."),
        "required": ["background", "aim", "method", "results", "conclusion"],
        "recommended": ["keywords"],
        "numbered": False,
        "visuals": False,
        "prose": False,
        "typical_words": 300,
        "skeleton": ["Background", "Aim", "Method", "Results", "Conclusion"],
        "checklist": [
            "Within the submission word limit, usually 250-300 words",
            "Results stated, not promised as 'will be discussed'",
            "Self-contained: no unexplained abbreviations",
        ],
    },
    "poster": {
        "label": "Academic poster",
        "aliases": ["academic-poster", "conference-poster", "poster-presentation"],
        "purpose": ("A single page read standing a metre away. Sections are "
                    "short, the data is visual, and the whole argument must "
                    "fit on one sheet."),
        "required": [("introduction", "background"), "aim", "method",
                     "results", "conclusion", "references"],
        "recommended": ["limitations"],
        "numbered": False,
        "visuals": True,
        "prose": False,
        "output": "pptx",
        "builder": "poster",
        "typical_words": 800,
        "skeleton": ["Introduction", "Aim", "Method", "Results",
                     "Conclusion", "References"],
        "checklist": [
            "Poster size confirmed against the brief (A0 or A1)",
            "Readable from one metre: body text 24pt or larger",
            "At least one chart, table or image carrying the findings",
            "Text kept short - a poster is not an essay in columns",
            "Student ID in the banner, no name (anonymous marking)",
            "Printed proof checked at full size before submission",
        ],
    },
    "presentation": {
        "label": "Presentation / slide deck",
        "aliases": ["slides", "slide-deck", "viva", "seminar"],
        "purpose": ("Spoken assessment. The slides carry key points; the "
                    "detail belongs in the speaker notes and in what is "
                    "said aloud."),
        "required": ["introduction", "conclusion", "references"],
        "recommended": ["aim", "method", "results", "discussion"],
        "numbered": False,
        "visuals": True,
        "prose": False,
        "output": "pptx",
        "builder": "presentation",
        "typical_words": 1200,
        "skeleton": ["Introduction", "Aim", "Method", "Findings",
                     "Discussion", "Conclusion", "References"],
        "checklist": [
            "Timed against the allowed length by rehearsing aloud",
            "Six points per slide at most - the rest goes in the notes",
            "Every claim on a slide carries its citation",
            "Full reference list included as the closing slide",
            "Student ID on the title slide, no name",
        ],
    },
    "book-review": {
        # "critique" now belongs to critical-review: in health and social care
        # a critique is almost always of a research article, not a book.
        "label": "Book review",
        "aliases": ["book-critique"],
        "max_section_share": {"summary_of_book": 0.45},
        "purpose": ("Academic evaluation of a published scholarly book's "
                    "argument, method and contribution."),
        "required": ["introduction", "summary_of_book", "discussion",
                     "conclusion", "references"],
        "recommended": [],
        "numbered": False,
        "visuals": False,
        "prose": True,
        "typical_words": 1200,
        "skeleton": ["Introduction", "Summary of the Book",
                     "Critical Evaluation", "Conclusion", "References"],
        "checklist": [
            "Full bibliographic details of the book given at the start",
            "Author's credentials and standpoint noted",
            "Summary kept shorter than the evaluation",
            "Judgement supported by evidence from the text, with page numbers",
            "Situated against other works in the field",
        ],
    },

    # ======================================================================
    # Types added for health and social care programmes. Each maps to modules
    # in the BSc (Hons) Health, Wellbeing and Social Care, and each carries a
    # "guide": the share of the word count and the guidance written into the
    # Word template under each heading.
    # ======================================================================

    "reflective-essay": {
        "label": "Reflective essay",
        "aliases": ["reflection", "reflective-account", "reflective-writing"],
        "purpose": ("Learning from an experience, structured by a named "
                    "reflective model. First person is expected, but the "
                    "reflection must still be analytical and supported by "
                    "literature - description alone earns little."),
        "required": ["introduction", "description", "discussion",
                     "action_plan", "conclusion", "references"],
        "recommended": ["feelings"],
        "numbered": False,
        "visuals": False,
        "prose": True,
        "first_person": "expected",
        "expects_terms": [REFLECTIVE_MODELS],
        "max_section_share": {"description": 0.30},
        "typical_words": 1500,
        "skeleton": ["Introduction", "Description", "Feelings", "Evaluation",
                     "Analysis", "Conclusion", "Action Plan", "References"],
        "guide": {
            "Introduction": (10, "Name the experience, the setting (anonymised) "
                             "and the reflective model you are using - Gibbs "
                             "(1988) is shown here. Driscoll's What? / So what? "
                             "/ Now what? works too; rename the headings."),
            "Description": (15, "What happened, briefly and factually. Keep this "
                            "SHORT: the marks are in the analysis, and "
                            "over-long description is the commonest fault."),
            "Feelings": (10, "What you thought and felt at the time, honestly. "
                         "First person is expected here."),
            "Evaluation": (15, "What went well and what did not, and why."),
            "Analysis": (30, "The heart of the piece. Make sense of the "
                         "experience using theory and research - every point "
                         "here should carry a citation."),
            "Conclusion": (10, "What you learned, and what you could have done "
                           "differently."),
            "Action Plan": (10, "Specific, dated steps you will take so the "
                            "learning changes your practice."),
        },
        "checklist": [
            "Reflective model named and cited, and its stages followed",
            "Description kept to around a sixth of the word count",
            "Analysis links the experience to theory and research",
            "First person used, in a professional register",
            "Service users, staff and setting fully anonymised",
            "Action plan specific enough to act on",
        ],
    },
    "critical-review": {
        "label": "Critical review / article critique",
        "aliases": ["critique", "critical-appraisal", "article-critique",
                    "research-critique", "journal-critique"],
        "purpose": ("A structured appraisal of a research article: what it "
                    "found, how far its methods justify its conclusions, and "
                    "what it means for practice."),
        "required": ["introduction", "summary_of_article", "appraisal",
                     "conclusion", "references"],
        "recommended": ["recommendations"],
        "numbered": False,
        "visuals": False,
        "prose": True,
        "expects_terms": [APPRAISAL_TOOLS],
        "max_section_share": {"summary_of_article": 0.35},
        "typical_words": 1500,
        "skeleton": ["Introduction", "Summary of the Article",
                     "Critical Appraisal", "Implications for Practice",
                     "Conclusion", "References"],
        "guide": {
            "Introduction": (10, "Give the full reference of the article, its "
                             "aim, and the appraisal tool you will use (for "
                             "example the CASP checklist for its design)."),
            "Summary of the Article": (20, "What the study did and found. Keep "
                                       "it brief - the marks are in the "
                                       "critique that follows."),
            "Critical Appraisal": (45, "Work through the appraisal tool: design, "
                                   "sampling, ethics, data collection, "
                                   "analysis, bias, validity and reliability. "
                                   "Weigh strengths against weaknesses, citing "
                                   "methods texts to support each judgement."),
            "Implications for Practice": (15, "What a practitioner should, and "
                                          "should not, take from this study."),
            "Conclusion": (10, "Your overall judgement of how far the "
                           "conclusions can be trusted."),
        },
        "checklist": [
            "Full reference of the article given at the start",
            "Appraisal tool matched to the study design and cited",
            "Summary shorter than the critique",
            "Each judgement supported by a methods source",
            "Implications for practice stated",
        ],
    },
    "policy-brief": {
        "label": "Policy brief",
        "aliases": ["briefing-paper", "policy-briefing"],
        "purpose": ("A short, persuasive document for a decision-maker: the "
                    "problem, the options, and a clear recommendation."),
        "required": ["abstract", "background", "policy_options",
                     "recommendations", "references"],
        "recommended": ["conclusion"],
        "numbered": None,
        "visuals": True,
        "prose": False,
        "typical_words": 1500,
        "skeleton": ["Executive Summary", "The Problem and its Context",
                     "Policy Options", "Recommendations", "Conclusion",
                     "References"],
        "guide": {
            "Executive Summary": (10, "The whole brief in a few lines: the "
                                  "problem, why it matters now, and what you "
                                  "recommend. Many readers stop here."),
            "The Problem and its Context": (30, "The scale of the issue, with "
                                            "current data, and the policy "
                                            "landscape it sits in."),
            "Policy Options": (30, "Two or three realistic options, each "
                               "weighed for cost, feasibility, equity and "
                               "evidence of effect."),
            "Recommendations": (25, "Your preferred option and exactly who "
                                "should do what. Short, direct, actionable."),
            "Conclusion": (5, "One closing paragraph."),
        },
        "checklist": [
            "Written for a named audience (minister, board, commissioner)",
            "Executive summary stands alone",
            "At least one chart or table showing the scale of the problem",
            "Options compared fairly before one is recommended",
            "Plain language - a brief is not an essay",
        ],
    },
    "policy-analysis": {
        "label": "Policy analysis / review",
        "aliases": ["policy-review", "policy-critique", "policy-evaluation"],
        "purpose": ("A critical analysis of a policy - its origins, content, "
                    "actors and effects - using a recognised framework."),
        "required": ["introduction", "background", "discussion",
                     "conclusion", "references"],
        "recommended": ["recommendations"],
        "numbered": None,
        "visuals": False,
        "prose": False,
        "expects_terms": [POLICY_FRAMEWORKS],
        "typical_words": 2500,
        "skeleton": ["Introduction", "Policy Background and Context",
                     "Analytical Framework", "Analysis of the Policy",
                     "Evaluation", "Conclusion", "Recommendations",
                     "References"],
        "guide": {
            "Introduction": (10, "Name the policy, its date and its purpose, "
                             "and say how you will analyse it."),
            "Policy Background and Context": (15, "Why the policy arose: the "
                                              "problem, the politics and the "
                                              "evidence at the time."),
            "Analytical Framework": (10, "The framework you are using - for "
                                     "example Walt and Gilson's policy "
                                     "triangle (context, content, process, "
                                     "actors) - and why it suits this policy."),
            "Analysis of the Policy": (30, "Apply the framework systematically, "
                                       "with evidence for each element."),
            "Evaluation": (20, "Has the policy achieved its aims? For whom, "
                           "and who has been left out? Consider equity."),
            "Conclusion": (10, "Your overall judgement of the policy."),
            "Recommendations": (5, "What should change, and who could change it."),
        },
        "checklist": [
            "Policy framework named, cited and applied throughout",
            "Context covers the political as well as the health drivers",
            "Effects on equality and vulnerable groups considered",
            "Analysis rather than description of the policy's contents",
        ],
    },
    "service-evaluation": {
        "label": "Service evaluation / quality improvement report",
        "aliases": ["quality-improvement", "qi-report", "qi-project",
                    "service-improvement", "audit-report", "clinical-audit",
                    "quality-report"],
        "purpose": ("An evaluation of a service or an improvement to it, "
                    "reported to the SQUIRE 2.0 structure, with the "
                    "governance status - evaluation, audit or research - "
                    "made explicit."),
        "required": ["introduction", "aim", "method", "results",
                     "discussion", "conclusion", "recommendations",
                     "references"],
        "recommended": ["abstract", "background", "ethics", "limitations"],
        "numbered": True,
        "visuals": True,
        "prose": False,
        "expects_terms": [QI_FRAMEWORKS, GOVERNANCE],
        "typical_words": 3000,
        "skeleton": ["1. Introduction", "2. Background", "3. Aim and Objectives",
                     "4. Method", "5. Ethics and Governance", "6. Results",
                     "7. Discussion", "8. Conclusion", "9. Recommendations",
                     "References", "Appendices"],
        "guide": {
            "1. Introduction": (8, "The service, the problem, and why it "
                                "matters to service users."),
            "2. Background": (10, "Current evidence and the national standard "
                              "the service should meet (NICE, CQC, local "
                              "policy)."),
            "3. Aim and Objectives": (5, "What the evaluation or improvement "
                                      "set out to achieve, in measurable "
                                      "terms."),
            "4. Method": (15, "The framework used - PDSA cycles, the Model for "
                          "Improvement, or an audit cycle - the measures, and "
                          "how data were collected."),
            "5. Ethics and Governance": (5, "State whether this is a service "
                                         "evaluation, an audit or research, "
                                         "and so whether ethical approval was "
                                         "needed. The HRA decision tool "
                                         "settles this."),
            "6. Results": (20, "What changed, shown in a run chart or table. "
                           "Report, do not yet interpret."),
            "7. Discussion": (20, "What the results mean, against the "
                              "literature, and the limitations."),
            "8. Conclusion": (7, "Was the aim met?"),
            "9. Recommendations": (10, "Specific next steps, including the "
                                   "next PDSA cycle or re-audit date."),
        },
        "checklist": [
            "Governance status stated: service evaluation, audit or research",
            "Improvement framework named (PDSA, Model for Improvement, audit cycle)",
            "Measures defined before the results are reported",
            "Results shown in a run chart or table",
            "Report follows the SQUIRE 2.0 headings",
            "Recommendations include a re-evaluation date",
        ],
    },
    "systematic-review": {
        "label": "Systematic / scoping review",
        "aliases": ["scoping-review", "structured-review",
                    "systematic-literature-review"],
        "purpose": ("A reproducible review of the literature: a stated "
                    "question, a documented search, explicit criteria, "
                    "appraisal of every included study, and synthesis."),
        "required": ["abstract", "introduction", "method", "results",
                     "discussion", "conclusion", "references"],
        "recommended": ["limitations", "appraisal", "appendices"],
        "numbered": None,
        "visuals": True,
        "prose": False,
        "expects_terms": [REVIEW_STANDARDS, DATABASES, SEARCH_FRAMEWORKS,
                          APPRAISAL_TOOLS],
        "typical_words": 4000,
        "skeleton": ["Abstract", "Introduction", "Method: Search Strategy",
                     "Eligibility Criteria", "Critical Appraisal", "Results",
                     "Discussion", "Limitations", "Conclusion", "References",
                     "Appendices"],
        "guide": {
            "Abstract": (0, "Structured: background, method, results, "
                         "conclusion. Usually outside the word count."),
            "Introduction": (10, "The problem, why a review is needed, and the "
                             "review question framed with PICO or PEO."),
            "Method: Search Strategy": (15, "Databases searched (CINAHL, "
                                        "MEDLINE, PsycINFO...), dates, search "
                                        "terms with Boolean operators. Put the "
                                        "full search in an appendix."),
            "Eligibility Criteria": (5, "Inclusion and exclusion criteria, "
                                     "each with a reason."),
            "Critical Appraisal": (10, "The appraisal tool used for each study "
                                   "(CASP, JBI) and a summary table."),
            "Results": (25, "A PRISMA flow diagram, a table of included "
                        "studies, then the findings by theme."),
            "Discussion": (20, "What the body of evidence shows, how strong it "
                           "is, and what it means for practice."),
            "Limitations": (5, "Of the review itself, not only of the studies."),
            "Conclusion": (10, "The answer to the review question."),
        },
        "checklist": [
            "Question framed with PICO, PEO or SPIDER",
            "Databases, dates and search terms reported in full",
            "PRISMA flow diagram included",
            "Every included study appraised with a named tool",
            "Synthesis organised by theme, not study by study",
        ],
    },
    "capstone-project": {
        "label": "Capstone project",
        "aliases": ["capstone", "final-project", "independent-project"],
        "purpose": ("The final-year independent project, the largest single "
                    "piece of work in the degree. Structured here as a "
                    "dissertation; confirm the required format in the module "
                    "handbook, as a capstone can also be a literature-based "
                    "project or a service improvement project."),
        "required": ["abstract", "introduction", "literature_review",
                     "method", "results", "discussion", "conclusion",
                     "references"],
        "recommended": ["acknowledgements", "contents", "ethics",
                        "limitations", "recommendations", "appendices"],
        "numbered": True,
        "visuals": True,
        "prose": False,
        "contents_page": True,
        "typical_words": 8000,
        "skeleton": ["Abstract", "Acknowledgements", "Table of Contents",
                     "1. Introduction", "2. Literature Review",
                     "3. Methodology", "4. Findings", "5. Discussion",
                     "6. Conclusion and Recommendations", "References",
                     "Appendices"],
        "guide": {
            "Abstract": (0, "Aim, method, key findings and conclusion in "
                         "about 250 words. Written last; usually outside the "
                         "word count."),
            "1. Introduction": (10, "The topic, its importance to health and "
                                "social care, the aim and objectives, and a "
                                "map of the chapters."),
            "2. Literature Review": (25, "A thematic, critical review that ends "
                                     "by identifying the gap your project "
                                     "addresses."),
            "3. Methodology": (15, "Design, philosophy, method, sampling, "
                               "analysis and ethics - each justified."),
            "4. Findings": (20, "What you found, presented clearly with tables "
                            "or figures."),
            "5. Discussion": (20, "Findings interpreted against the literature, "
                              "with strengths and limitations."),
            "6. Conclusion and Recommendations": (10, "Answer the aim, and give "
                                                  "recommendations for "
                                                  "practice and research."),
        },
        "checklist": [
            "Format and word count confirmed in the capstone module handbook",
            "Supervisor agreement on the topic and method recorded",
            "Ethical approval in place before any data collection",
            "Contents page generated from the headings (Word: References > "
            "Table of Contents)",
            "Every appendix referred to from the main text",
        ],
    },
    "reflective-portfolio": {
        "label": "Reflective portfolio",
        "aliases": ["portfolio", "practice-portfolio", "work-based-learning",
                    "wbl-portfolio", "placement-portfolio"],
        "purpose": ("A collection of reflective entries from practice or "
                    "work-based learning, each using a reflective model, "
                    "brought together in a development plan."),
        "required": ["introduction", "reflective_entries", "action_plan",
                     "conclusion", "references"],
        "recommended": ["appendices"],
        "numbered": False,
        "visuals": False,
        "prose": False,
        "first_person": "expected",
        "expects_terms": [REFLECTIVE_MODELS],
        "min_entries": 2,
        "typical_words": 3000,
        "skeleton": ["Introduction", "Reflective Entry 1", "Reflective Entry 2",
                     "Reflective Entry 3", "Personal Development Plan",
                     "Conclusion", "References", "Appendices"],
        "guide": {
            "Introduction": (10, "The placement or work context (anonymised), "
                             "the period covered, and the reflective model "
                             "used."),
            "Reflective Entry 1": (22, "One experience, taken through every "
                                   "stage of your reflective model, linked to "
                                   "theory."),
            "Reflective Entry 2": (22, "A different experience - ideally one "
                                   "showing a different skill or value."),
            "Reflective Entry 3": (22, "A third experience, or a return to an "
                                   "earlier one showing how your practice "
                                   "changed."),
            "Personal Development Plan": (14, "Learning needs identified across "
                                          "the entries, with SMART goals and "
                                          "dates."),
            "Conclusion": (10, "How you have developed over the period."),
        },
        "checklist": [
            "Every entry follows the same named reflective model",
            "Entries cover different skills or values",
            "Every person and setting anonymised",
            "Evidence (certificates, feedback) in the appendices",
            "Development plan draws on all the entries",
        ],
    },
    "personal-development-plan": {
        "label": "Personal development plan",
        "aliases": ["pdp", "development-plan", "career-plan"],
        "purpose": ("An honest self-assessment turned into SMART goals and a "
                    "dated action plan for professional development."),
        "required": ["introduction", "self_assessment", "goals",
                     "action_plan", "references"],
        "recommended": ["conclusion"],
        "numbered": None,
        "visuals": True,
        "prose": False,
        "first_person": "expected",
        "expects_terms": [SWOT, SMART],
        "typical_words": 1500,
        "skeleton": ["Introduction", "Self-Assessment (SWOT)",
                     "Development Goals", "Action Plan",
                     "Review and Evaluation", "Conclusion", "References"],
        "guide": {
            "Introduction": (10, "Where you are now and the career direction "
                             "you are working towards."),
            "Self-Assessment (SWOT)": (25, "Strengths, weaknesses, "
                                       "opportunities and threats, each "
                                       "supported by evidence, ideally in a "
                                       "table."),
            "Development Goals": (20, "Three to five SMART goals drawn from the "
                                  "SWOT."),
            "Action Plan": (25, "A table: goal, action, resources, deadline, "
                            "how success will be measured."),
            "Review and Evaluation": (10, "How and when you will review "
                                      "progress."),
            "Conclusion": (10, "What the plan will achieve for you."),
        },
        "checklist": [
            "SWOT supported with evidence, not assertion",
            "Every goal is SMART",
            "Action plan set out as a table with dates",
            "Linked to relevant professional standards or job roles",
        ],
    },
    "health-promotion-plan": {
        "label": "Health promotion plan",
        "aliases": ["health-promotion", "health-campaign", "intervention-plan"],
        "purpose": ("A planned health promotion intervention: the need, the "
                    "theory behind the approach, implementation and "
                    "evaluation."),
        "required": ["introduction", "needs_assessment", "aim",
                     "implementation", "evaluation_plan", "references"],
        "recommended": ["conclusion", "ethics"],
        "numbered": None,
        "visuals": True,
        "prose": False,
        "expects_terms": [HEALTH_PROMOTION_MODELS, SMART],
        "typical_words": 2500,
        "skeleton": ["1. Introduction", "2. Needs Assessment",
                     "3. Aim and SMART Objectives", "4. Health Promotion Model",
                     "5. Intervention and Implementation", "6. Evaluation",
                     "7. Conclusion", "References"],
        "guide": {
            "1. Introduction": (8, "The health issue and the community the "
                                "intervention is for."),
            "2. Needs Assessment": (20, "Local and national data showing the "
                                    "need - and who experiences it most. "
                                    "Consider the wider determinants of "
                                    "health."),
            "3. Aim and SMART Objectives": (10, "One aim; objectives that are "
                                            "specific, measurable, achievable, "
                                            "relevant and time-bound."),
            "4. Health Promotion Model": (17, "The model or theory guiding the "
                                          "approach - Tannahill, Beattie, the "
                                          "Health Belief Model, COM-B - and "
                                          "why it fits."),
            "5. Intervention and Implementation": (20, "What will be done, by "
                                                   "whom, where and when."),
            "6. Evaluation": (15, "How you will know it worked: process, "
                              "impact and outcome measures."),
            "7. Conclusion": (10, "Summary of the plan and its expected "
                              "benefit."),
        },
        "checklist": [
            "Need established with local data, not assumption",
            "Health promotion model named, cited and applied",
            "Objectives are SMART",
            "Evaluation measures defined before implementation",
            "Health inequalities addressed",
        ],
    },
    "ethical-dilemma-analysis": {
        "label": "Ethical dilemma analysis",
        "aliases": ["ethical-analysis", "ethics-case", "ethical-dilemma"],
        "purpose": ("Analysis of an ethical dilemma from practice using "
                    "ethical theory, and the law and professional codes that "
                    "bound the decision."),
        "required": ["introduction", "dilemma", "discussion", "conclusion",
                     "references"],
        "recommended": ["legal_framework", "confidentiality"],
        "numbered": False,
        "visuals": False,
        "prose": True,
        "expects_terms": [ETHICAL_FRAMEWORKS, LEGAL_FRAMEWORKS],
        "typical_words": 2000,
        "skeleton": ["Introduction", "Confidentiality Statement", "The Dilemma",
                     "Ethical Analysis", "Legal and Professional Framework",
                     "Conclusion", "References"],
        "guide": {
            "Introduction": (10, "What the piece will analyse and the "
                             "frameworks it will use."),
            "Confidentiality Statement": (2, "State that names and identifying "
                                          "details have been changed."),
            "The Dilemma": (18, "The situation, and why the competing duties "
                            "cannot all be met."),
            "Ethical Analysis": (35, "Apply ethical theory - Beauchamp and "
                                 "Childress's four principles, deontology, "
                                 "utilitarianism - to each option."),
            "Legal and Professional Framework": (25, "The law and codes that "
                                                 "apply - the Mental Capacity "
                                                 "Act 2005, the Care Act 2014, "
                                                 "a professional code of "
                                                 "conduct."),
            "Conclusion": (10, "The most defensible course of action, and why."),
        },
        "checklist": [
            "Confidentiality maintained throughout",
            "Competing principles identified explicitly",
            "At least two ethical perspectives applied",
            "Relevant legislation cited with its correct title and year",
            "Clear, justified conclusion",
        ],
    },
    "change-management-proposal": {
        "label": "Change management proposal",
        "aliases": ["change-proposal", "change-management",
                    "leadership-proposal", "business-case",
                    "improvement-proposal"],
        "purpose": ("A proposal for a change in a health or social care "
                    "setting, using a change model and leadership theory, "
                    "with a plan for implementation and evaluation."),
        "required": ["introduction", "background", "change_model",
                     "implementation", "evaluation_plan", "conclusion",
                     "references"],
        "recommended": ["stakeholders", "recommendations"],
        "numbered": True,
        "visuals": True,
        "prose": False,
        "expects_terms": [CHANGE_MODELS],
        "typical_words": 2500,
        "skeleton": ["1. Introduction", "2. Background and Rationale for Change",
                     "3. Stakeholder Analysis", "4. Change Model",
                     "5. Implementation Plan", "6. Evaluation",
                     "7. Conclusion and Recommendations", "References"],
        "guide": {
            "1. Introduction": (8, "The proposed change and the setting."),
            "2. Background and Rationale for Change": (17, "The problem, the "
                                                       "evidence that change "
                                                       "is needed, and the "
                                                       "cost of doing "
                                                       "nothing."),
            "3. Stakeholder Analysis": (15, "Who is affected and how much "
                                        "influence they have - a power / "
                                        "interest grid works well."),
            "4. Change Model": (20, "The change model - Lewin, Kotter, ADKAR - "
                                "applied stage by stage, with the leadership "
                                "style needed."),
            "5. Implementation Plan": (20, "Actions, owners and dates - a Gantt "
                                       "chart or table."),
            "6. Evaluation": (10, "How success will be measured and sustained."),
            "7. Conclusion and Recommendations": (10, "The case for the change, "
                                                  "summarised."),
        },
        "checklist": [
            "Change model named, cited and applied stage by stage",
            "Stakeholders and likely resistance identified",
            "Leadership theory linked to the change",
            "Implementation timeline shown in a table or Gantt chart",
            "Evaluation measures defined",
        ],
    },
    "debate-position-paper": {
        "label": "Debate position paper",
        "aliases": ["debate", "position-paper", "debate-brief"],
        "purpose": ("Preparation for an assessed debate: a clear position, "
                    "the evidence for it, and prepared rebuttals to the "
                    "strongest opposing arguments."),
        "required": ["introduction", "arguments_for", "counter_arguments",
                     "conclusion", "references"],
        "recommended": [],
        "numbered": False,
        "visuals": False,
        "prose": True,
        "typical_words": 1000,
        "skeleton": ["Introduction and Position", "Arguments For",
                     "Counter-Arguments and Rebuttal", "Conclusion",
                     "References"],
        "guide": {
            "Introduction and Position": (15, "The motion, and your position "
                                          "on it in one sentence."),
            "Arguments For": (40, "Your three strongest arguments, each with "
                              "its best evidence."),
            "Counter-Arguments and Rebuttal": (35, "The strongest case against "
                                               "you, and your answer to "
                                               "each point."),
            "Conclusion": (10, "Why your position should prevail."),
        },
        "checklist": [
            "Position stated in one clear sentence",
            "Each argument backed by cited evidence",
            "The strongest opposing arguments anticipated, not the weakest",
            "Rebuttals prepared for the question-and-answer element",
        ],
    },

    # ----------------------------------------------------------------------
    # Types taken from real module handbooks and rubrics, rather than
    # from general knowledge of UK assessment. The section names below are
    # the names his markers use.
    # ----------------------------------------------------------------------
    "care-plan": {
        "label": "Care plan",
        "aliases": ["care-plan-template", "patient-care-plan", "support-plan",
                    "care-planning"],
        "purpose": ("A plan for one service user: what they need, what will "
                    "be done, by whom, and when it is reviewed. Written about "
                    "a real or given scenario, never about a named person."),
        "required": ["service_user", "needs", "goals", "mdt", "references"],
        "recommended": ["introduction", "capacity", "safeguarding",
                        "confidentiality", "review", "conclusion"],
        "numbered": False,
        "visuals": False,
        "prose": False,
        "typical_words": 2000,
        "expects_terms": [SMART, PERSON_CENTRED, LEGAL_FRAMEWORKS,
                          CARE_PLAN_PARTS],
        "skeleton": ["Introduction", "Service User Statement",
                     "Examples of Needs", "SMART Goals and Interventions",
                     "Capacity and Consent",
                     "Confidentiality and Safeguarding",
                     "The Multidisciplinary Team and the Professional's Role",
                     "Review and Evaluation", "Conclusion", "References"],
        "guide": {
            "Introduction": (8, "What the care plan is for, who it concerns "
                             "(pseudonym only) and what the reader will find."),
            "Service User Statement": (14, "The person in their own terms: "
                                       "background, what matters to them, "
                                       "their routines, culture, faith and "
                                       "preferences, and their strengths. The "
                                       "rest of the plan must follow from "
                                       "this, or it is not person-centred."),
            "Examples of Needs": (16, "Their needs across physical, emotional "
                                  "and social aspects. Say why each is a "
                                  "priority, and for whom."),
            "SMART Goals and Interventions": (20, "For each need: a goal that "
                                              "is Specific, Measurable, "
                                              "Achievable, Relevant and "
                                              "Time-bound, and the "
                                              "intervention that meets it. "
                                              "Link each one back to a named "
                                              "need."),
            "Capacity and Consent": (10, "How capacity is assumed and "
                                     "assessed (Mental Capacity Act 2005), "
                                     "how consent was sought, and what "
                                     "happens if capacity is lacking."),
            "Confidentiality and Safeguarding": (10, "What may be shared, "
                                                 "with whom and why (Data "
                                                 "Protection Act 2018, "
                                                 "Caldicott), and the "
                                                 "safeguarding duties that "
                                                 "apply."),
            "The Multidisciplinary Team and the Professional's Role": (
                12, "Who else is involved, what each contributes, and where "
                "your own professional boundaries lie."),
            "Review and Evaluation": (6, "When the plan is reviewed, how "
                                      "progress is measured, and who decides."),
            "Conclusion": (4, "What the plan achieves for this person."),
        },
        "checklist": [
            "Pseudonym used; no detail that could identify a person or setting",
            "The Service User Statement drives the needs, goals and "
            "interventions",
            "Every goal is SMART, with a date and a measure",
            "Each intervention names the need it answers",
            "Legislation named and applied, not just listed (Care Act 2014, "
            "Mental Capacity Act 2005, Equality Act 2010, Data Protection "
            "Act 2018)",
            "Cultural, religious and dietary needs considered explicitly",
            "Review arrangements stated",
        ],
    },
    "reflective-journal": {
        "label": "Reflective journal entry",
        "aliases": ["journal", "journal-entry", "learning-journal",
                    "reflective-journal-entry", "weekly-reflection"],
        "purpose": ("A short weekly reflection, usually 150 to 200 words, "
                    "on one experience or idea. Short, honest and specific, "
                    "not an essay."),
        "required": ["reflection_what", "reflection_so_what",
                     "reflection_now_what"],
        "recommended": ["references"],
        "numbered": False,
        "visuals": False,
        "prose": True,
        "first_person": "expected",
        "expects_terms": [REFLECTIVE_MODELS],
        "max_section_share": {"reflection_what": 0.40},
        "typical_words": 200,
        "skeleton": ["What happened?", "So what?", "Now what?"],
        "guide": {
            "What happened?": (30, "What happened, or what the idea is. Two "
                               "or three sentences. This is the part that "
                               "must stay short."),
            "So what?": (40, "Why it matters for practice, and what it "
                         "changed in your understanding. Link to one source "
                         "if the task asks for it."),
            "Now what?": (30, "What you will do differently, specifically "
                          "enough that someone could check whether you did "
                          "it."),
        },
        "checklist": [
            "Within the word limit the task sets (usually 150 to 200 words)",
            "First person, and honest rather than tidy",
            "The 'Now what?' names a concrete next step, not a good intention",
            "Rolfe, Gibbs or Driscoll named if the task asks for a model",
            "Anonymous: no real names of people or placements",
        ],
    },
    "presentation-script": {
        "label": "Presentation or video script",
        "aliases": ["script", "video-script", "oral-presentation-script",
                    "recorded-presentation", "voiceover"],
        "purpose": ("The words you will say in a recorded or live "
                    "presentation, timed to the slides. Marked on the "
                    "content, not the delivery alone."),
        "required": ["introduction", "conclusion", "references"],
        "recommended": ["aim", "discussion", "recommendations"],
        "numbered": False,
        "visuals": False,
        "prose": True,
        "typical_words": 2250,
        "skeleton": ["Slide 1: Introduction and Aim", "Slide 2: Context",
                     "Main Section: Evidence", "Main Section: Analysis",
                     "Recommendation or Intervention", "Conclusion",
                     "References"],
        "guide": {
            "Slide 1: Introduction and Aim": (5, "What the presentation is "
                                              "about and what the audience "
                                              "will take away. About 30 "
                                              "seconds."),
            "Slide 2: Context": (15, "Why the topic matters, with the data or "
                                 "policy that frames it."),
            "Main Section: Evidence": (25, "The evidence, cited as you speak: "
                                       "'Smith, in 2020, found...'. The "
                                       "citation must also appear on the "
                                       "slide."),
            "Main Section: Analysis": (25, "What the evidence means, where it "
                                       "disagrees, and its limits. This is "
                                       "where the marks are."),
            "Recommendation or Intervention": (20, "What should change, for "
                                               "whom, and how it would be "
                                               "evaluated."),
            "Conclusion": (10, "The one message you want remembered."),
        },
        "checklist": [
            "Timed: about 150 words a minute, so 15 minutes is about 2,250 "
            "words. Read it aloud with a timer",
            "Every citation spoken also appears on the slide it belongs to",
            "Written to be spoken: short sentences, no subclauses",
            "Signposted: say what is coming, then say it",
            "Slide numbers in the script match the deck",
            "A source cited only in the notes is not cited at all",
        ],
    },
    "health-leaflet": {
        "label": "Health promotion leaflet",
        "aliases": ["leaflet", "information-leaflet", "health-promotion-leaflet",
                    "flyer", "patient-information"],
        "purpose": ("Written for the public, not for a marker: plain "
                    "language, one message, and somewhere to go next."),
        "required": ["references"],
        "recommended": ["aim", "recommendations"],
        "numbered": False,
        "visuals": True,
        "prose": False,
        "expects_terms": [PLAIN_LANGUAGE, HEALTH_PROMOTION_MODELS],
        "typical_words": 500,
        "skeleton": ["Title and who this is for", "What it is",
                     "Why it matters to you", "What you can do",
                     "Where to get help", "References"],
        "guide": {
            "Title and who this is for": (10, "A title the reader would say "
                                          "themselves, and who the leaflet is "
                                          "aimed at."),
            "What it is": (20, "The condition or issue in everyday words. No "
                           "jargon; if a medical term is needed, explain it "
                           "in the same sentence."),
            "Why it matters to you": (20, "The risk or benefit, in terms the "
                                      "reader recognises from their own life."),
            "What you can do": (30, "Two or three actions, specific and "
                                "achievable. Respect cost, culture, food and "
                                "routine."),
            "Where to get help": (15, "Named services, with how to reach "
                                  "them, including what is free."),
            "References": (5, "Small print at the end, still in your referencing style."),
        },
        "checklist": [
            "Reading age of about 9 to 11: short words, short sentences, "
            "active voice",
            "Speaks to the reader as 'you'",
            "One main message, not five",
            "Culturally appropriate advice (food, fasting, gender, language)",
            "Says where to get help, and what it costs",
            "Evidence behind the advice cited, even if in small print",
            "Accessible: plain font, good contrast, space around the text",
        ],
    },
    "search-strategy-report": {
        "label": "Literature search strategy report",
        "aliases": ["search-strategy", "literature-search",
                    "database-search-report", "evidence-search"],
        "purpose": ("Shows how you found the evidence and how good it is: "
                    "the search, the appraisal of the study designs, and "
                    "what the findings mean for practice."),
        "required": ["introduction", "method", "discussion", "conclusion",
                     "references"],
        "recommended": ["aim", "limitations", "recommendations"],
        "numbered": True,
        "visuals": True,
        "prose": False,
        "expects_terms": [DATABASES, SEARCH_FRAMEWORKS, SEARCH_MECHANICS,
                          APPRAISAL_TOOLS],
        "typical_words": 2500,
        "skeleton": ["1. Introduction", "2. Search Strategy",
                     "3. Critical Analysis of Study Designs",
                     "4. Application to Practice", "5. Conclusion",
                     "References", "Appendices"],
        "guide": {
            "1. Introduction": (10, "The question, and why it matters to "
                                "practice. PICO or SPIDER if the brief asks."),
            "2. Search Strategy": (30, "Databases searched, keywords and "
                                   "Boolean operators, inclusion and "
                                   "exclusion criteria with reasons, dates, "
                                   "and how many results at each stage. "
                                   "Justify the choices; evaluating the "
                                   "alternatives is where higher marks sit."),
            "3. Critical Analysis of Study Designs": (30, "For each study: "
                                                      "the design, its "
                                                      "strengths and "
                                                      "limitations, and how "
                                                      "the design affects "
                                                      "reliability and "
                                                      "validity. Use a named "
                                                      "appraisal tool."),
            "4. Application to Practice": (20, "What the studies together "
                                           "mean for practice. Compare them; "
                                           "do not summarise them one by one."),
            "5. Conclusion": (10, "What you found, and how good the evidence "
                              "is."),
        },
        "checklist": [
            "Databases named, with dates of the search",
            "Search terms and Boolean operators shown, often as a table",
            "Inclusion and exclusion criteria justified, not just listed",
            "Numbers of results at each stage (a PRISMA-style flow helps)",
            "A named appraisal tool applied to each study",
            "Studies compared with each other, not listed in turn",
            "Alternatives to the chosen search approach evaluated",
        ],
    },
}


def output_format(type_key):
    """Which file a type is delivered as: 'docx' (default) or 'pptx'."""
    canonical = resolve_type(type_key)
    if canonical is None:
        return "docx"
    return DOCUMENT_TYPES[canonical].get("output", "docx")


def builder_kind(type_key):
    """For a pptx type, whether to build a 'poster' or a 'presentation'."""
    canonical = resolve_type(type_key)
    if canonical is None:
        return None
    return DOCUMENT_TYPES[canonical].get("builder")


def _requirement_label(requirement):
    """A requirement is one section key, or a tuple of acceptable ones."""
    keys = requirement if isinstance(requirement, (tuple, list)) \
        else (requirement,)
    return " or ".join(SECTION_LABELS.get(k, k) for k in keys)


def _an(word):
    """'an essay', 'a report' - pick the right indefinite article."""
    return ("an " if word[:1].lower() in "aeiou" else "a ") + word


def resolve_type(name):
    """Accept a type name or any of its aliases. Returns the canonical key."""
    if not name:
        return None
    key = name.strip().lower().replace("_", "-").replace(" ", "-")
    if key in DOCUMENT_TYPES:
        return key
    for canonical, spec in DOCUMENT_TYPES.items():
        if key in spec["aliases"]:
            return canonical
    # Tolerate a trailing plural, e.g. "essays" or "debates".
    if key.endswith("s") and len(key) > 1:
        return resolve_type(key[:-1])
    return None


def _body_text(text):
    """The draft without its reference list, title and HTML comments."""
    text = re.sub(r"<!--.*?-->", "", text, flags=re.DOTALL)
    m = re.search(r"(?:^|\n)[ \t]*#*[ \t]*(?:References?|Bibliography|"
                  r"Reference\s+List)[ \t]*:?[ \t]*\n", text,
                  flags=re.IGNORECASE)
    return text[:m.start()] if m else text


def section_word_shares(text):
    """Share of the body's words falling under each known section key."""
    body = _body_text(text)
    counts = {}
    total = 0
    current = []
    for line in body.splitlines():
        m = re.match(r"^(#{2,6})\s+(.*\S)\s*$", line)
        if m:
            clean = re.sub(r"[*_`]", "", m.group(2)).strip()
            clean = re.sub(r"^(?:\d+(?:\.\d+)*|[A-Z]|[ivxlc]+)[.)]\s*", "",
                           clean, flags=re.IGNORECASE).strip()
            current = [k for k in SECTION_SYNONYMS
                       if _matches_section(clean, k)]
            continue
        if line.lstrip().startswith("#"):
            continue
        words = len(re.findall(r"[A-Za-z0-9']+", line))
        total += words
        for key in current:
            counts[key] = counts.get(key, 0) + words
    if not total:
        return {}
    return {k: v / float(total) for k, v in counts.items()}


def count_matching_headings(text, section_key):
    return sum(1 for level, clean, raw in extract_headings(text)
               if _matches_section(clean, section_key))


# --------------------------------------------------------------------------
# Section guidance, for templates and skeletons
# --------------------------------------------------------------------------

GENERIC_GUIDANCE = {
    "abstract": "A standalone summary: purpose, approach, key findings and "
                "conclusion. Write it last. Usually outside the word count - "
                "check the brief.",
    "introduction": "The topic, why it matters to health and social care, and "
                    "exactly what this piece will do. End with your argument "
                    "or aim.",
    "background": "The context the reader needs - setting, scale of the "
                  "issue, the policy or service involved - with evidence.",
    "literature_review": "What is already known, organised by theme rather "
                         "than author by author. Show where evidence agrees, "
                         "conflicts and runs out.",
    "aim": "The aim in one sentence, then the objectives or the question.",
    "method": "How the work was done and why, in enough detail to repeat it.",
    "results": "What was found, without interpretation yet. Use a table or "
               "figure where it helps.",
    "discussion": "Interpret against the literature. Every paragraph answers "
                  "'so what?' - its meaning for practice, and its limits.",
    "conclusion": "Draw the argument together and answer the question. No new "
                  "evidence here.",
    "recommendations": "Specific, realistic actions that follow from the "
                       "findings - who should do what.",
    "limitations": "The weaknesses of this work, and how they affect the "
                   "conclusions.",
    "ethics": "Consent, confidentiality, data storage and any approval needed.",
    "confidentiality": "State that names and identifying details have been "
                       "changed to protect confidentiality.",
    "timeline": "The stages of the work with dates, ideally as a table.",
    "summary_of_book": "What the book argues and how. Keep it shorter than "
                       "your evaluation.",
    "keywords": "Four to six keywords.",
    "acknowledgements": "Optional. Not counted.",
    "contents": "Generated by Word from the headings: References > Table of "
                "Contents. Update it before submitting.",
    "appendices": "Supporting material referred to in the text. Not counted.",
    # Any heading that is not a recognised section, such as "Main Body".
    None: "Develop the argument in themed paragraphs, each making one point, "
          "supported by two or three sources, and ending in your own "
          "evaluation - the 'so what?' test. Never end a paragraph on a "
          "citation.",
    "references": "Your referencing style, in alphabetical order. Every source cited in "
                  "the text appears here, and nothing else.",
}

# Sections that normally sit outside the word count.
UNCOUNTED = ("references", "appendices", "abstract", "acknowledgements",
             "contents", "keywords")


def _section_key_for(heading):
    """The section key a skeleton heading represents, for generic guidance."""
    clean = re.sub(r"^(?:\d+(?:\.\d+)*)[.)]\s*", "", heading).strip()
    # Most specific keys first, so "Critical Appraisal" is not read as a
    # generic discussion and "References" is never read as anything else.
    order = ["references", "appendices", "abstract", "contents",
             "acknowledgements", "keywords", "confidentiality", "ethics",
             "limitations", "timeline", "literature_review", "summary_of_book",
             # Care plans and journals, whose headings are named by his rubrics.
             "service_user", "goals", "mdt", "capacity", "safeguarding",
             "reflection_so_what", "reflection_now_what", "reflection_what",
             "needs",
             "introduction", "aim", "method", "results", "recommendations",
             "conclusion", "background", "discussion", "review"]
    for key in order:
        if _matches_section(clean, key):
            return key
    return None


def section_guide(type_key, target_words=None):
    """[(heading, words, share_percent, guidance)] for each skeleton section.

    Types added for health and social care carry hand-written guidance and
    word shares. The original types fall back to generic guidance, with the
    counted words split evenly after giving the introduction and conclusion
    10% each. The brief always overrides these figures.
    """
    canonical = resolve_type(type_key)
    if canonical is None:
        return []
    spec = DOCUMENT_TYPES[canonical]
    target = target_words or spec["typical_words"]
    guide = spec.get("guide", {})

    rows = []
    if guide:
        for heading in spec["skeleton"]:
            share, text = guide.get(heading, (0, None))
            if text is None:
                key = _section_key_for(heading)
                text = GENERIC_GUIDANCE.get(key, "")
            rows.append((heading, share, text))
    else:
        keys = [_section_key_for(h) for h in spec["skeleton"]]
        counted = [i for i, k in enumerate(keys) if k not in UNCOUNTED]
        fixed = {i: 10 for i in counted if keys[i] in ("introduction",
                                                         "conclusion")}
        rest = [i for i in counted if i not in fixed]
        remaining = 100 - sum(fixed.values())
        each = remaining / float(len(rest)) if rest else 0
        for i, heading in enumerate(spec["skeleton"]):
            if i in fixed:
                share = fixed[i]
            elif i in rest:
                share = each
            else:
                share = 0
            rows.append((heading, share,
                         GENERIC_GUIDANCE.get(keys[i], "")))

    return [(h, int(round(target * s / 100.0)), s, g) for h, s, g in rows]


def type_names():
    out = []
    for key, spec in sorted(DOCUMENT_TYPES.items()):
        aliases = (" (%s)" % ", ".join(spec["aliases"])) if spec["aliases"] else ""
        out.append(key + aliases)
    return out


# --------------------------------------------------------------------------
# Reading a draft
# --------------------------------------------------------------------------

def extract_headings(text):
    """Every markdown heading, as (level, cleaned_text, raw_text)."""
    headings = []
    for line in text.splitlines():
        m = re.match(r"^(#{1,6})\s+(.*\S)\s*$", line)
        if not m:
            continue
        raw = m.group(2).strip()
        clean = re.sub(r"[*_`]", "", raw).strip()
        # Drop a leading number, letter or roman numeral label.
        clean = re.sub(r"^(?:\d+(?:\.\d+)*|[A-Z]|[ivxlc]+)[.)]\s*", "",
                       clean, flags=re.IGNORECASE).strip()
        from .layout import to_english
        clean = to_english(clean)        # a heading in any of the languages
        headings.append((len(m.group(1)), clean, raw))
    return headings


def _matches_section(heading_text, section_key):
    patterns = SECTION_SYNONYMS.get(section_key, [section_key])
    text = heading_text.strip().lower()
    for pattern in patterns:
        if re.match(r"^" + pattern + r"\b", text):
            return True
        if re.search(r"\b" + pattern + r"\b", text):
            return True
    return False


def find_sections(text):
    """Map each known section key to the heading that satisfies it."""
    headings = extract_headings(text)
    present = {}
    for key in SECTION_SYNONYMS:
        for level, clean, raw in headings:
            if _matches_section(clean, key):
                present.setdefault(key, raw)
                break
    return present, headings


def has_visual_aid(text):
    """A markdown table, or a captioned table or figure."""
    if re.search(r"^\s*\|.+\|\s*$", text, flags=re.MULTILINE):
        return True
    if re.search(r"\b(?:table|figure|fig\.|chart|graph)\s*\d+", text,
                 flags=re.IGNORECASE):
        return True
    if re.search(r"!\[[^\]]*\]\([^)]+\)", text):
        return True
    return False


def numbered_section_ratio(text):
    """Proportion of level-2 headings that carry a section number."""
    level2 = [raw for level, clean, raw in extract_headings(text)
              if level == 2]
    if not level2:
        return 0.0, 0
    numbered = [r for r in level2
                if re.match(r"^\s*\d+(?:\.\d+)*[.)]?\s+\S", r)]
    return len(numbered) / float(len(level2)), len(level2)


def bullet_ratio(text):
    """Proportion of body lines that are bullets, ignoring the references."""
    m = re.search(r"(?:^|\n)[ \t]*#*[ \t]*(?:References?|Bibliography)",
                  text, flags=re.IGNORECASE)
    body = text[:m.start()] if m else text
    lines = [l for l in body.splitlines() if l.strip()
             and not l.lstrip().startswith("#")]
    if not lines:
        return 0.0
    bullets = [l for l in lines
               if re.match(r"^\s*(?:[-*+]|\d+[.)])\s+\S", l)]
    return len(bullets) / float(len(lines))


def count_annotations(text):
    """For an annotated bibliography: entries, and how many have annotations.

    An entry is a line beginning with a capitalised author or institution and
    carrying a bracketed year. Its annotation is the prose that follows before
    the next entry.
    """
    m = re.search(r"(?:^|\n)[ \t]*#*[ \t]*(?:References?|Bibliography|"
                  r"Annotated\s+Entries|Entries)[ \t]*:?[ \t]*\n",
                  text, flags=re.IGNORECASE)
    section = text[m.end():] if m else text

    blocks = [b.strip() for b in re.split(r"\n\s*\n", section) if b.strip()]
    entry_re = re.compile(r"^[*_`]*[A-Z][^\n]*?\((?:\d{4}[a-z]?|n\.d\.)\)")

    entries, annotated = 0, 0
    i = 0
    while i < len(blocks):
        if entry_re.match(re.sub(r"[*_`]", "", blocks[i])):
            entries += 1
            # An annotation is either prose in the next block, or prose
            # inside this block after the reference's own full stop.
            has_annotation = False
            if i + 1 < len(blocks) and not entry_re.match(
                    re.sub(r"[*_`]", "", blocks[i + 1])):
                if len(blocks[i + 1].split()) >= 15:
                    has_annotation = True
                    i += 1
            if not has_annotation:
                # Same-block annotation: a long entry with sentences after
                # the bibliographic data.
                words = len(blocks[i].split())
                if words >= 60:
                    has_annotation = True
            if has_annotation:
                annotated += 1
        i += 1

    return entries, annotated


# --------------------------------------------------------------------------
# The check
# --------------------------------------------------------------------------

def check_structure(text, type_key):
    """Check a draft against its document type.

    Returns a dict with 'failures', 'warnings', 'passes' (lists of strings)
    and 'ok' (True when there are no failures).
    """
    canonical = resolve_type(type_key)
    if canonical is None:
        return {
            "type": None,
            "ok": False,
            "failures": ["unknown document type %r - known types: %s"
                         % (type_key, ", ".join(sorted(DOCUMENT_TYPES)))],
            "warnings": [],
            "passes": [],
        }

    spec = DOCUMENT_TYPES[canonical]
    present, headings = find_sections(text)

    failures, warnings, passes = [], [], []

    for requirement in spec["required"]:
        keys = requirement if isinstance(requirement, (tuple, list)) \
            else (requirement,)
        label = _requirement_label(requirement)
        satisfied = next((k for k in keys if k in present), None)
        if satisfied:
            passes.append("%s present (\"%s\")"
                          % (label, present[satisfied][:40]))
        else:
            failures.append("%s section is MISSING" % label)

    for requirement in spec.get("recommended", []):
        keys = requirement if isinstance(requirement, (tuple, list)) \
            else (requirement,)
        label = _requirement_label(requirement)
        if any(k in present for k in keys):
            passes.append("%s present" % label)
        else:
            warnings.append("%s not found - expected in %s"
                            % (label, _an(spec["label"].lower())))

    # Numbered sections.
    ratio, total = numbered_section_ratio(text)
    if spec["numbered"] is True:
        if total == 0:
            warnings.append("no level-2 headings found to number")
        elif ratio < 0.6:
            failures.append("sections are not numbered (%d of %d are) - %s "
                            "uses numbered sections"
                            % (int(ratio * total), total,
                               _an(spec["label"].lower())))
        else:
            passes.append("sections numbered (%d of %d)"
                          % (int(ratio * total), total))
    elif spec["numbered"] is False and total and ratio > 0.4:
        warnings.append("headings are numbered, but %s should not use "
                        "numbered sections" % _an(spec["label"].lower()))

    # Visual aids.
    if spec["visuals"]:
        if has_visual_aid(text):
            passes.append("at least one table or figure present")
        else:
            warnings.append("no table or figure found - %s normally "
                            "presents data visually"
                            % _an(spec["label"].lower()))

    # Continuous prose.
    if spec.get("prose"):
        ratio_b = bullet_ratio(text)
        if ratio_b > 0.25:
            warnings.append("%.0f%% of body lines are bullets - %s should "
                            "be continuous prose"
                            % (ratio_b * 100, _an(spec["label"].lower())))
        else:
            passes.append("written as continuous prose")

    # Annotated bibliography: one annotation per entry.
    if spec.get("annotations_required"):
        entries, annotated = count_annotations(text)
        if entries == 0:
            failures.append("no reference entries found to annotate")
        elif annotated < entries:
            failures.append("%d of %d entries have no annotation"
                            % (entries - annotated, entries))
        else:
            passes.append("all %d entries carry an annotation" % entries)

    body = _body_text(text)

    # Named frameworks and models a marker will look for.
    for label, patterns in spec.get("expects_terms", []):
        if any(re.search(p, body, flags=re.IGNORECASE) for p in patterns):
            passes.append("found %s" % label.split(" (")[0])
        else:
            warnings.append("no mention of %s" % label)

    # A section allowed only a limited share of the words. The reflective
    # essay's description is the case that matters: over-long description is
    # the "descriptive narrative" fault the marker flagged in HWSC200.
    shares = section_word_shares(text)
    for key, limit in spec.get("max_section_share", {}).items():
        share = shares.get(key)
        if share is None:
            continue
        label = SECTION_LABELS.get(key, key)
        if share > limit:
            warnings.append("%s takes %.0f%% of the words - keep it under "
                            "%.0f%% and move the effort into analysis"
                            % (label, share * 100, limit * 100))
        else:
            passes.append("%s kept to %.0f%% of the words"
                          % (label, share * 100))

    # First person: expected in reflective writing.
    if spec.get("first_person") == "expected":
        uses = len(re.findall(r"\b(?:I|me|my|myself|I'm|I've|I’m)\b",
                              body))
        if uses < 3:
            warnings.append("hardly any first person (%d use%s) - reflective "
                            "writing is expected to say 'I'"
                            % (uses, "" if uses == 1 else "s"))
        else:
            passes.append("written in the first person")

    # A portfolio needs more than one entry.
    minimum = spec.get("min_entries")
    if minimum:
        entries = count_matching_headings(text, "reflective_entries")
        if entries < minimum:
            failures.append("%d reflective entr%s found - a portfolio needs "
                            "at least %d"
                            % (entries, "y" if entries == 1 else "ies",
                               minimum))
        else:
            passes.append("%d reflective entries" % entries)

    return {
        "type": canonical,
        "label": spec["label"],
        "ok": not failures,
        "failures": failures,
        "warnings": warnings,
        "passes": passes,
    }


def build_skeleton(type_key, title="[Title]", student_id="[Student ID]",
                   target_words=None, language=None):
    """The markdown skeleton written into the drafts folder by setup_exam.py.

    Each section carries its guidance and a suggested length as an HTML
    comment, which the word counter and the builders both ignore.
    """
    canonical = resolve_type(type_key)
    if canonical is None:
        return None
    spec = DOCUMENT_TYPES[canonical]
    target = target_words or spec["typical_words"]
    from .layout import current, heading as _h, labels
    language = language or current()["language"]

    lines = [
        "# %s" % title,
        "",
        "%s: %s" % (labels(language)["student_id"], student_id),
        "",
        "<!-- %s: %s -->" % (spec["label"], spec["purpose"]),
        "<!-- Target length: %s words. The assignment brief overrides this. -->"
        % format(target, ","),
        "",
    ]
    for heading, words, share, guidance in section_guide(canonical, target):
        lines.append("## %s" % _h(heading, language))
        lines.append("")
        if words:
            lines.append("<!-- About %s words (%d%%). %s -->"
                         % (format(words, ","), round(share), guidance))
        elif guidance:
            lines.append("<!-- %s -->" % guidance)
        lines.append("")
    return "\n".join(lines)


def checklist_lines(type_key):
    """Type-specific checklist items for EXAM_CONFIG.md."""
    canonical = resolve_type(type_key)
    if canonical is None:
        return []
    spec = DOCUMENT_TYPES[canonical]
    lines = ["## %s Requirements" % spec["label"],
             "*%s*" % spec["purpose"], ""]
    for item in spec["checklist"]:
        lines.append("- [ ] %s" % item)
    lines.append("")
    lines.append("### Required sections")
    for requirement in spec["required"]:
        lines.append("- [ ] %s" % _requirement_label(requirement))
    if spec.get("recommended"):
        lines.append("")
        lines.append("### Expected, but not mandatory")
        for requirement in spec["recommended"]:
            lines.append("- [ ] %s" % _requirement_label(requirement))
    return lines


def print_type(type_key):
    canonical = resolve_type(type_key)
    if canonical is None:
        print("Unknown type %r. Known types:" % type_key)
        for name in type_names():
            print("  %s" % name)
        return False
    spec = DOCUMENT_TYPES[canonical]
    print("=" * 70)
    print("  %s  (%s)" % (spec["label"], canonical))
    print("=" * 70)
    print("  %s" % spec["purpose"])
    print("\n  Required sections:")
    for requirement in spec["required"]:
        print("    - %s" % _requirement_label(requirement))
    if spec.get("recommended"):
        print("\n  Expected but not mandatory:")
        for requirement in spec["recommended"]:
            print("    - %s" % _requirement_label(requirement))
    print("\n  Numbered sections: %s" % {True: "yes", False: "no",
                                         None: "either"}[spec["numbered"]])
    print("  Tables or figures: %s" % ("expected" if spec["visuals"] else "not required"))
    print("  Continuous prose:  %s" % ("expected" if spec.get("prose") else "not required"))
    print("  Typical length:    %s words (the brief overrides this)"
          % format(spec["typical_words"], ","))
    print("  Delivered as:      .%s" % spec.get("output", "docx"))
    return True


def print_check(result, verbose=True):
    if not verbose:
        return
    print("  Document type: %s" % (result.get("label") or "unknown"))
    for item in result["passes"]:
        print("  [OK]      %s" % item)
    for item in result["warnings"]:
        print("  [CHECK]   %s" % item)
    for item in result["failures"]:
        print("  [FIX]     %s" % item)
    if result["ok"] and not result["warnings"]:
        print("  The structure is the one this document type asks for.")
    elif result["failures"]:
        print("  Do: add the missing sections, with the headings above.")


if __name__ == "__main__":
    if len(sys.argv) == 1:
        print("Document types the harness knows (%d):" % len(DOCUMENT_TYPES))
        for key, spec in sorted(DOCUMENT_TYPES.items()):
            aliases = ("  aliases: %s" % ", ".join(spec["aliases"])) \
                if spec["aliases"] else ""
            print("  %-24s %s%s" % (key, spec["label"], aliases))
        print("\nUsage: python document_types.py <type> [draft.md]")
        sys.exit(0)

    type_key = sys.argv[1]
    if len(sys.argv) == 2:
        sys.exit(0 if print_type(type_key) else 1)

    path = sys.argv[2]
    with open(path, "r", encoding="utf-8", errors="replace") as f:
        text = f.read()
    print("=" * 70)
    print("  DOCUMENT STRUCTURE CHECK - %s" % os.path.basename(path))
    print("=" * 70)
    result = check_structure(text, type_key)
    print_check(result)
    sys.exit(0 if result["ok"] else 1)
