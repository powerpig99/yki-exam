# YKI Personalized Learning Material — Production Process

A replicable framework for generating personalized YKI exam preparation materials as Finnish karaoke videos with parallel English translations.

---

## Overview

| Phase | Input | Output | Time |
|-------|-------|--------|------|
| 1. Intake | Blank questionnaire | Filled profile | 30 min (learner) |
| 2. Research | YKI level + profile | Topic requirements doc | 1-2 hours |
| 3. Planning | Profile + topics | Content plan with item list | 1-2 hours |
| 4. Production | Plan + pipeline | Karaoke videos | ~2 min per video |
| 5. Review | Videos | Approved final set | Ongoing |

---

## Phase 1: Intake

1. Give learner `questionnaire.md`
2. Learner fills it out in any language
3. Save as `learners/<name>/profile.md`
4. Review answers, note key themes for personalization

**Key personalization dimensions:**
- Cultural background (holidays, food, family dynamics)
- Professional context (field, job search challenges)
- Daily life specifics (transport, neighborhood, routines)
- Immigration journey (authorities dealt with, challenges faced)
- Social situation (family, friends, community)
- Goals and motivations (why YKI, future plans)

---

## Phase 2: Research

### 2a. Official Requirements
- YKI test structure and scoring for target level
- Official topic lists from YKI/Opetushallitus
- Required skills per section (puhuminen, kirjoittaminen, kuunteleminen, lukeminen)

### 2b. Public Test Experiences
- Blog posts from test-takers (search: "YKI kokemus", "YKI keskitaso kokemuksia")
- YouTube videos with test tips/experiences (transcribe using free services)
- Forum discussions (Suomi24, Reddit r/Finland, Facebook groups)
- Pattern analysis: common topics, surprising questions, frequent pitfalls

### 2c. Gap Analysis
- Compare official topics + discovered topics against planned content
- Identify any uncovered areas
- Adjust topic plan accordingly

Save as `learners/framework/yki_keskitaso_topics.md` (shared across learners).

---

## Phase 3: Planning

### Content Types
| Type | Format | Description |
|------|--------|-------------|
| Dialogi (dia) | A↔B conversation | Real-life situational dialogues |
| Reagointi (rea) | A=situation, B=response | Quick reactions to everyday scenarios |
| Kertominen (ker) | A=questions, B=narrative | Extended speaking on personal topics |
| Mielipide (mie) | A=statement, B=opinion | Opinion/argument on social topics |
| Kirjoittaminen (wri) | A=task, B=written text | T1=informal, T2=formal, T3=both opinions |

### Topic Coverage
One comprehensive set per learner:
- All standard YKI keskitaso topics (equivalent to textbook chapters, rephrased)
- Supplementary topics from research
- Everything personalized to learner's profile

### Planning Output
A structured plan with:
- Topic list organized by theme
- Item IDs and descriptions
- Estimated total videos
- Processing order

Save plan in Claude plans or as `learners/<name>/plan.md`.

---

## Phase 4: Production

### File Format: fi_en_package.md
```markdown
#### <id> — <type>: <topic>
**FI Konteksti:** <Finnish context>
**EN Context:** <English context>

**FI Koko mallidialogi:**
- **A**: <Finnish text>
- **B**: <Finnish text>
...

**EN Full sample dialogue:**
- **A**: <English text>
- **B**: <English text>
...
```

### Critical Rules
1. **Sentence count match**: Each FI turn and its EN translation MUST have identical sentence counts (split on `.` `!` `?`). Mismatches break subtitle sync.
2. **Finnish names only**: Use generic Finnish names (Matti, Sanna, Mikko, Laura, etc.) for TTS compatibility. Never use foreign names.
3. **No English abbreviations**: Use Finnish equivalents in FI text (tekoäly not AI, koneoppiminen not ML). Keep English in EN translation.
4. **Finnicize loanwords**: freelance → friilanssi, online → verkossa. Better TTS pronunciation.
5. **Natural spoken Finnish**: YKI keskitaso level, colloquial (mä/mun/sun), not overly formal.
6. **One topic per A-B exchange**: Don't split topics across multiple exchanges.

### Pipeline Commands
```bash
# Generate TTS (Google Chirp 3 HD)
.venv/bin/python3 scripts/generate_dialog_tts_google.py --only <id> --force

# Render karaoke video
.venv/bin/python3 scripts/render_dialog_karaoke.py --only <id> --force
```

### Per-Item Workflow (STRICT ORDER)
1. Write fi_en_package.md
2. Generate TTS
3. Render video
4. Present for review
5. **After approval** (do ALL, even if feedback given):
   a. Copy video to delivery folder
   b. git add + commit + push
   c. Update progress tracking
   d. Address any feedback
   e. Proceed to next item

---

## Phase 5: Review & Delivery

- Learner reviews each video for:
  - Content accuracy and relatability
  - Audio quality (no TTS glitches)
  - Subtitle sync (FI karaoke + EN translation)
- Fix issues before proceeding
- Copy approved videos to delivery folder (iCloud, Google Drive, etc.)

---

## Folder Structure

```
learners/
  framework/
    questionnaire.md          # Standard intake form
    process.md                # This file
    yki_keskitaso_topics.md   # Research-based topic requirements (shared)
  <name>/
    profile.md                # Filled questionnaire
    plan.md                   # Content plan with all items
    dialogues/
      <id>/
        fi_en_package.md      # Source content
        audio/                # TTS output (git-ignored)
        video/                # Rendered video (git-ignored)
```

---

## Scaling Notes

### Multi-language Support
- Translation language is configurable (currently English)
- Questionnaire can be filled in any language
- Framework works for any YKI level (adjust topic research)
- Could extend beyond YKI to other language certification exams

### Quality Assurance
- One video at a time prevents cascading errors
- Sentence count verification catches subtitle sync issues early
- TTS voice rotation keeps content engaging
- User review catches content/cultural issues AI might miss

### Cost Estimation
- Google Chirp 3 HD: 1M free chars/month, ~5000 chars per video → ~200 videos/month free
- Video rendering: local ffmpeg, no cost
- Storage: ~7-15 MB per video, ~1-2 GB per learner set
