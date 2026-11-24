# Capability Author Checklist

## 1. Trigger conditions

- [ ] Is there a real `capability gap` record?
- [ ] Do both the local registry and trusted sources lack a suitable capability?
- [ ] Is the task finished, and is the flow repeatable?
- [ ] Is this not just "too lazy to look for a tool"?

## 2. Candidate content

- [ ] Is the `SKILL.md` name lowercase-hyphen, and does the description say
      "what it does + when to use it"?
- [ ] Does `SKILL.md` have When to use / Inputs / Workflow / Outputs / Checks /
      Failure handling?
- [ ] Does the workflow come from the task trace rather than being made up?
- [ ] Does every step have inputs/outputs/acceptance, instead of just "call the model"?
- [ ] Are secrets, tokens and private absolute paths kept out of the candidate?
- [ ] Are scripts inert skeletons: no network, no subprocess, read-only by default?

## 3. Card

- [ ] Is the description at least 20 characters, specific, and not marketing copy?
- [ ] Are there 3-5 concrete examples?
- [ ] Are capabilities few and accurate, with evidence?
- [ ] Are permissions minimal; is net/write/shell omitted where there is no evidence?
- [ ] Is the invoke mode correct; does the content_path exist in prompt mode?
- [ ] Does the probe have at least static checks; will behavioral tasks be
      backfilled by the host?

## 4. Approval and verification

- [ ] Has a human reviewed SKILL.md, examples, permissions and scripts?
- [ ] After `synthesize approve`, does the registry contain that id?
- [ ] Does `capability probe` pass (or is the failure reason explicit)?
- [ ] Does the real task `capability outcome` record success/failure, duration
      and human edit minutes?
- [ ] Is "installed" not mistaken for "usable"?

## 5. Improvement

- [ ] Did `improve` read the outcomes, rather than follow a hunch?
- [ ] Does the patch have a version number, evidence and change notes?
- [ ] Are installed skills left untouched by default; is `--apply` an explicit action?
- [ ] Do Card updates still go through review/apply?
