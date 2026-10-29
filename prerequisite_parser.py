from __future__ import annotations
import re
from dataclasses import dataclass, field
from typing import List, Optional, Set, Dict, Any, Tuple

# -----------------------------------------------------------------
# 5-Stage Prerequisite Logic Compiler & AST Evaluator
# -----------------------------------------------------------------

@dataclass
class LogicNode:
    """Represents a node in the Boolean Abstract Syntax Tree (AST)."""
    op: str  # 'AND', 'OR', 'NOT', 'COURSE', 'QUANTIFIER', 'NULL'
    value: Optional[str] = None
    children: List[LogicNode] = field(default_factory=list)
    count: Optional[int] = None

    def to_dict(self) -> Optional[Dict[str, Any]]:
        if self.op == "NULL":
            return None
        if self.op == "COURSE":
            parts = (self.value or "").strip().split(" ", 1)
            return {
                "type": "COURSE",
                "course": self.value,
                "subject": parts[0] if parts else None,
                "number": parts[1] if len(parts) > 1 else None,
            }
        if self.op == "QUANTIFIER":
            return {
                "type": "MIN_COURSES_IN_SUBJECT",
                "subject": self.value,
                "min_count": self.count or 1,
            }
        if self.op == "NOT":
            child = self.children[0].to_dict() if self.children else None
            return {"type": "NOT", "operand": child} if child else None
        if self.op in ("AND", "OR"):
            valid_children = []
            for c in self.children:
                d = c.to_dict()
                if d is not None:
                    valid_children.append(d)
            if not valid_children:
                return None
            if len(valid_children) == 1:
                return valid_children[0]
            return {"type": self.op, "operands": valid_children}
        return None

    def to_formula(self) -> str:
        if self.op == "NULL":
            return ""
        if self.op == "COURSE":
            return self.value or ""
        if self.op == "QUANTIFIER":
            return f"MIN_{self.count}_OF({self.value})"
        if self.op == "NOT":
            inner = self.children[0].to_formula() if self.children else ""
            return f"NOT ({inner})" if inner else ""
        if self.op in ("AND", "OR"):
            parts = [c.to_formula() for c in self.children if c.op != "NULL"]
            parts = [p for p in parts if p]
            if not parts:
                return ""
            if len(parts) == 1:
                return parts[0]
            return f"({f' {self.op} '.join(parts)})"
        return ""


def normalize_id(cid: str) -> str:
    return str(cid).replace(" ", "").strip().upper()


def format_id(cid: str) -> str:
    clean = normalize_id(cid)
    m = re.match(r"^([A-Z]+)(\d.*)$", clean)
    return f"{m.group(1)} {m.group(2)}" if m else cid


class GeneralizedPrerequisiteCompiler:
    """
    Five-stage compiler for academic course prerequisites and corequisites.
    Implements domain-aware precedence, contraction expansion, elided subject
    propagation, and incremental requirement evaluation.
    """
    CONTRACTIONS = [
        (r"\bhaven't\b", "have not"),
        (r"\bhasn't\b", "has not"),
        (r"\bhadn't\b", "had not"),
        (r"\bwouldn't\b", "would not"),
        (r"\bcouldn't\b", "could not"),
        (r"\bshouldn't\b", "should not"),
        (r"\bcan't\b", "can not"),
        (r"\bcannot\b", "can not"),
        (r"\bdon't\b", "do not"),
        (r"\bdoesn't\b", "does not"),
        (r"\bdidn't\b", "did not"),
        (r"\bisn't\b", "is not"),
        (r"\baren't\b", "are not"),
        (r"\bwon't\b", "will not"),
        (r"\bmustn't\b", "must not"),
    ]

    PERMISSION_PATTERN = re.compile(
        r"\b(?:permission\s+of\s+(?:the\s+)?(?:instructor|department|program\s+chair)|"
        r"instructor(?:'s)?\s+permission|departmental\s+permission|"
        r"consent\s+of\s+(?:the\s+)?(?:instructor|department)|"
        r"instructor(?:'s)?\s+consent|by\s+permission|or\s+permission|with\s+permission)\b",
        re.IGNORECASE,
    )

    # Strictly matches 3-digit course numbers with optional academic suffixes (e.g., 110, 114L, 200G)
    COURSE_RE = re.compile(r"\b([A-Za-z]{2,6})(?:\s+|-)(\d{3}[A-Za-z]?)\b")

    def expand_contractions(self, text: str) -> str:
        out = text
        for pat, repl in self.CONTRACTIONS:
            out = re.sub(pat, repl, out, flags=re.IGNORECASE)
        return out

    def _extract_negations(self, text: str) -> Tuple[str, List[LogicNode], List[LogicNode]]:
        positive_nodes: List[LogicNode] = []
        negative_nodes: List[LogicNode] = []
        cleaned = text

        # 1. Double-negative mandatory: "students who have not taken X may not enroll" -> X is positive prereq
        double_neg_re = re.compile(
            r"students\s+who\s+(?:have|had|do|did)\s+not\s+(?:taken|completed|passed|have)\s+"
            r"([^;]+?)\s+(?:may|can|will|would|should)\s+not\s+"
            r"(?:enroll(?:[\s]+in)?|take|register(?:[\s]+for)?|receive\s+credit(?:[\s]+for)?)"
            r"(?:[\s]+[A-Za-z]{2,6}[\s]+\d{3}[A-Za-z]?)?\.?",
            re.IGNORECASE,
        )
        for m in double_neg_re.finditer(cleaned):
            courses = self.COURSE_RE.findall(m.group(1))
            for dept, num in courses:
                positive_nodes.append(LogicNode(op="COURSE", value=f"{dept.upper()} {num.upper()}"))
            cleaned = cleaned.replace(m.group(0), " ")

        # 2. Direct prior-completion exclusion (anti-requisite):
        # "Students who have taken BIOL 254 may not enroll in BIOL 252" -> NOT(BIOL 254)
        anti_req_re = re.compile(
            r"students\s+(?:who|that)\s+(?:have\s+taken|took|complete|completed|have\s+credit\s+for)\s+"
            r"([^.:;]+?)\s+(?:may\s+not|can\s+not|will\s+not|would\s+not|are\s+not\s+eligible\s+to)\s+"
            r"(?:enroll(?:[\s]+in)?|take|register(?:[\s]+for)?|receive\s+credit(?:[\s]+for)?)"
            r"(?:[\s]+[A-Za-z]{2,6}[\s]+\d{3}[A-Za-z]?)?\.?",
            re.IGNORECASE,
        )
        for m in anti_req_re.finditer(cleaned):
            courses = self.COURSE_RE.findall(m.group(1))
            for dept, num in courses:
                c_node = LogicNode(op="COURSE", value=f"{dept.upper()} {num.upper()}")
                negative_nodes.append(LogicNode(op="NOT", children=[c_node]))
            cleaned = cleaned.replace(m.group(0), " ")

        # 3. Repeat category exclusion: "may not take more than one 200G course"
        repeat_re = re.compile(
            r"students\s+(?:may|can|will|would|should)\s+not\s+take\s+more\s+than\s+one\s+"
            r"(\d{3}[A-Za-z]?)\s*(?:\([^)]*\))?\s*course\.?",
            re.IGNORECASE,
        )
        for m in repeat_re.finditer(cleaned):
            c_node = LogicNode(op="COURSE", value=m.group(1).upper())
            negative_nodes.append(LogicNode(op="NOT", children=[c_node]))
            cleaned = cleaned.replace(m.group(0), " ")

        # 4. Credit exclusion: "not open to students who have taken X"
        not_open_re = re.compile(
            r"(?:not\s+open\s+to\s+students\s+who\s+have\s+taken|no\s+credit\s+if\s+taken)\s+"
            r"([A-Za-z]{2,6}\s+\d{3}[A-Za-z]?(?:\s*(?:or|and|/)\s*(?:[A-Za-z]{2,6}\s+)?\d{3}[A-Za-z]?)*)\.?",
            re.IGNORECASE,
        )
        for m in not_open_re.finditer(cleaned):
            courses = self.COURSE_RE.findall(m.group(1))
            for dept, num in courses:
                c_node = LogicNode(op="COURSE", value=f"{dept.upper()} {num.upper()}")
                negative_nodes.append(LogicNode(op="NOT", children=[c_node]))
            cleaned = cleaned.replace(m.group(0), " ")

        return cleaned, positive_nodes, negative_nodes

    def _strip_administrative_noise(self, text: str) -> str:
        s = text
        # Date windows
        s = re.sub(r"Open\s+to\s+[^.]*?\s+until\s+\d{1,2}/\d{1,2}/\d{2,4}\.?", " ", s, flags=re.IGNORECASE)
        s = re.sub(r"Other\s+majors\s+may\s+register\s+starting(?:\s+on)?\s+\d{1,2}/\d{1,2}/\d{2,4}\.?", " ", s, flags=re.IGNORECASE)
        s = re.sub(r"Guest/non-degree\s+students\s+may\s+register\s+starting\s+\d{1,2}/\d{1,2}/\d{2,4}\.?", " ", s, flags=re.IGNORECASE)

        # Placement waivers & tests
        s = re.sub(
            r"(?:,\s*|\s+or\s+|\s+and\s+)?placement\s+into\s+[A-Za-z]{2,6}\s+\d{3}[A-Za-z]?\s+"
            r"(?:on|from)\s+the\s+math\s+placement\s+(?:test|exam)",
            " ",
            s,
            flags=re.IGNORECASE,
        )
        s = re.sub(r"score\s+of\s+\d+(?:\s+or\s+\d+)?\s+[A-Za-z]+\s+on\s+Writing\s+Assessment(?:\s+or\b)?", " ", s, flags=re.IGNORECASE)

        # Grade / Equivalent noise
        s = re.sub(r"\bgrade\s+of\s+[A-DF][+-]?\s+or\s+(?:better|higher)\s+in\b", " ", s, flags=re.IGNORECASE)
        s = re.sub(r"\bor\s+(?:higher|equivalent|the\s+equivalent)\b", " ", s, flags=re.IGNORECASE)

        # Standing & degree cohorts
        s = re.sub(r"\b(?:Freshman|Sophomore|Junior|Senior)(?:\s+level)?\s+standing\b", " ", s, flags=re.IGNORECASE)
        s = re.sub(r"\b(?:Graduate|Undergraduate)\s+(?:degree\s+)?students?\s+in\s+[A-Za-z\s]+?(?=\bor\b|\.|$)", " ", s, flags=re.IGNORECASE)
        s = re.sub(r"\b[A-Za-z\s,]+\s+(?:PhD|MS|MA|BS|BA|certificate)\s+students\b\.?", " ", s, flags=re.IGNORECASE)
        s = re.sub(r"\b(?:ABA\s+certificate|Degree)\s+students?(?:\s+only)?\.?", " ", s, flags=re.IGNORECASE)
        s = re.sub(r"\b[A-Za-z]{2,6}\s+students\s+only\.?", " ", s, flags=re.IGNORECASE)
        s = re.sub(r"\b[A-Z]{3,8}-(?:MA|MS|PHD|BA|BS|CERT|MEd)\b", " ", s, flags=re.IGNORECASE)

        # Credit minimums (e.g., "and 60 credits", "a minimum of 60 credits", "60 or more credits")
        s = re.sub(
            r"(?:,\s*|\s+and\s+)?\b(?:at\s+least\s+|a\s+minimum\s+of\s+)?\d+\s*(?:or\s+more\s+)?(?:earned\s+)?credits?\b",
            " ",
            s,
            flags=re.IGNORECASE,
        )

        # Header prefixes
        s = re.sub(r"^\s*(?:Pre-?requisites?|Pre-?req|Requisites?)\s*(?::|=)\s*", " ", s, flags=re.IGNORECASE)
        return s

    def _segment_modalities(self, text: str) -> Tuple[str, str, str]:
        either_clause = ""
        coreq_clause = ""
        prereq_clause = text

        # 1. Dual-modality (EITHER: Pre-req or co-req)
        either_match = re.search(
            r"(?:;\s*|\b(?:and\s+)?)?(?:Pre-?req(?:uisite)?\s+or\s+co-?req(?:uisite)?|"
            r"Co-?req(?:uisite)?\s*\(\s*or\s+pre-?req(?:uisite)?\s*\))\s*(?::|=)\s*"
            r"(.*?)(?=(?:;\s*)?(?:\b(?:and\s+)?(?:Co-?requisite|Co\s*-\s*req|Co-?req)\b|$))",
            prereq_clause,
            flags=re.IGNORECASE,
        )
        if either_match:
            either_clause = either_match.group(1).strip()
            prereq_clause = prereq_clause[:either_match.start()] + " " + prereq_clause[either_match.end():]

        # 2. Strict Corequisites (COREQ)
        coreq_match = re.search(
            r"(?:;\s*|\b(?:and\s+)?)?(?:Co-?requisites?|Co\s*-\s*req|Co-?req)\s*(?::|=|\s+or\b)\s*(.*)",
            prereq_clause,
            flags=re.IGNORECASE,
        )
        if coreq_match:
            coreq_clause = coreq_match.group(1).strip()
            prereq_clause = prereq_clause[:coreq_match.start()].strip()

        return prereq_clause.strip(), coreq_clause.strip(), either_clause.strip()

    def _normalize_tokens(self, text: str) -> List[str]:
        if not text:
            return []

        s = text
        # Slash course disjunctions: BIOL 252/254 -> (BIOL 252 or BIOL 254)
        s = re.sub(
            r"\b([A-Za-z]{2,6})\s*(\d{3}[A-Za-z]?)/(\d{3}[A-Za-z]?)\b",
            r"(\1 \2 or \1 \3)",
            s,
        )
        s = re.sub(
            r"\b(\d{3}[A-Za-z]?)/(\d{3}[A-Za-z]?)\b",
            r"(\1 or \2)",
            s,
        )

        # "with" between course pairs: CHEM 252 with 256 -> (CHEM 252 AND_TIGHT 256)
        s = re.sub(
            r"\b([A-Za-z]{2,6}\s+\d{3}[A-Za-z]?)\s+with\s+(\d{3}[A-Za-z]?(?:\s+or\s+\d{3}[A-Za-z]?)*)",
            r"(\1 AND_TIGHT \2)",
            s,
            flags=re.IGNORECASE,
        )

        # Scoped "one of the following:" -> ( ... )
        s = re.sub(r"\bone\s+of\s+the\s+following\s*:\s*", "(", s, flags=re.IGNORECASE)

        # Quantifiers: 2 AMST courses -> QUANT_2_AMST
        s = re.sub(
            r"\b(\d+)\s+([A-Za-z]{2,6})\s+courses\b",
            lambda m: f"QUANT_{m.group(1)}_{m.group(2).upper()}",
            s,
            flags=re.IGNORECASE,
        )

        s = re.sub(r",\s*or\b", " OR_MACRO ", s, flags=re.IGNORECASE)
        s = re.sub(r",\s*and\b", " AND ", s, flags=re.IGNORECASE)
        s = re.sub(r"[;,]", " AND ", s)
        s = s.replace("(", " ( ").replace(")", " ) ")

        raw_words = s.split()
        tokens: List[str] = []
        current_dept: Optional[str] = None
        i = 0

        while i < len(raw_words):
            tok = raw_words[i].strip()
            if not tok:
                i += 1
                continue

            t_upper = tok.upper()
            if t_upper in ("(", ")"):
                tokens.append(t_upper)
            elif t_upper.startswith("QUANT_"):
                tokens.append(t_upper)
            elif t_upper in ("AND_TIGHT", "OR_MACRO"):
                tokens.append(t_upper)
            elif t_upper == "AND":
                tokens.append("AND")
            elif t_upper == "OR":
                tokens.append("OR_INTRA")
            elif re.match(r"^[A-Za-z]{2,6}$", tok):
                # Potential department code paired with 3-digit course number
                if i + 1 < len(raw_words) and re.match(r"^\d{3}[A-Za-z]?$", raw_words[i + 1]):
                    current_dept = t_upper
                    tokens.append(f"COURSE:{current_dept} {raw_words[i + 1].upper()}")
                    i += 2
                    continue
            elif re.match(r"^\d{3}[A-Za-z]?$", tok):
                # Guard against standalone numbers attached to unstripped credit keywords
                if i + 1 < len(raw_words) and re.match(r"^credits?\.?$", raw_words[i + 1], re.IGNORECASE):
                    i += 1
                    continue

                # Standalone 3-digit course number: propagate active department code
                if current_dept:
                    if tokens and tokens[-1] == "AND":
                        tokens[-1] = "AND_TIGHT"
                    tokens.append(f"COURSE:{current_dept} {t_upper}")
            i += 1

        # Post-process: promote 'AND' between two courses of same subject to 'AND_TIGHT'
        for idx in range(1, len(tokens) - 1):
            if tokens[idx] == "AND":
                left = tokens[idx - 1]
                right = tokens[idx + 1]
                if left.startswith("COURSE:") and right.startswith("COURSE:"):
                    d1 = left.split(":")[1].split()[0]
                    d2 = right.split(":")[1].split()[0]
                    if d1 == d2:
                        tokens[idx] = "AND_TIGHT"

        return tokens

    def _compile_shunting_yard(self, tokens: List[str]) -> Optional[LogicNode]:
        if not tokens:
            return None

        precedence = {
            "OR_MACRO": 1,
            "AND": 2,
            "OR_INTRA": 3,
            "AND_TIGHT": 4,
            "NOT": 5,
        }

        output_queue: List[LogicNode] = []
        op_stack: List[str] = []

        def apply_operator(op_tok: str):
            canon_op = "AND" if op_tok in ("AND", "AND_TIGHT") else ("OR" if op_tok in ("OR_INTRA", "OR_MACRO") else op_tok)
            if canon_op == "NOT":
                if output_queue:
                    operand = output_queue.pop()
                    output_queue.append(LogicNode(op="NOT", children=[operand]))
            else:
                if len(output_queue) >= 2:
                    r = output_queue.pop()
                    l = output_queue.pop()
                    output_queue.append(LogicNode(op=canon_op, children=[l, r]))
                elif output_queue:
                    output_queue.append(output_queue.pop())

        for tok in tokens:
            if tok.startswith("COURSE:"):
                output_queue.append(LogicNode(op="COURSE", value=tok[7:]))
            elif tok.startswith("QUANT_"):
                parts = tok.split("_")
                output_queue.append(LogicNode(op="QUANTIFIER", count=int(parts[1]), value=parts[2]))
            elif tok == "(":
                op_stack.append(tok)
            elif tok == ")":
                while op_stack and op_stack[-1] != "(":
                    apply_operator(op_stack.pop())
                if op_stack and op_stack[-1] == "(":
                    op_stack.pop()
            elif tok in precedence:
                while op_stack and op_stack[-1] in precedence and precedence[op_stack[-1]] >= precedence[tok]:
                    apply_operator(op_stack.pop())
                op_stack.append(tok)

        while op_stack:
            apply_operator(op_stack.pop())

        if not output_queue:
            return None

        def simplify(n: LogicNode) -> LogicNode:
            n.children = [simplify(c) for c in n.children if c.op != "NULL"]
            if n.op in ("AND", "OR"):
                flattened: List[LogicNode] = []
                for c in n.children:
                    if c.op == n.op:
                        flattened.extend(c.children)
                    else:
                        flattened.append(c)
                n.children = flattened
                if len(n.children) == 1:
                    return n.children[0]
            return n

        root = simplify(output_queue[-1])
        return root

    def parse(self, raw_text: Optional[str]) -> Dict[str, Any]:
        """Main parsing entrypoint compiling raw catalog prerequisite text into ASTs."""
        if not raw_text or str(raw_text).strip().lower() in ("none", "none.", "n/a", ""):
            return {
                "raw": raw_text or "None",
                "permission_allowed": False,
                "prerequisites_formula": None,
                "corequisites_formula": None,
                "prereq_or_coreq_formula": None,
                "anti_requisites_formula": None,
                "prerequisites_ast": None,
                "corequisites_ast": None,
                "prereq_or_coreq_ast": None,
                "anti_requisites_ast": None,
            }

        text = self.expand_contractions(str(raw_text))

        # Check instructor / department permission
        permission_allowed = bool(self.PERMISSION_PATTERN.search(text))
        text = self.PERMISSION_PATTERN.sub(" ", text)

        # Extract negative / anti-requisite constraints
        text, pos_nodes, neg_nodes = self._extract_negations(text)

        # Clean administrative sentences & placement exam noise
        cleaned_text = self._strip_administrative_noise(text)

        # Temporal modality partitioning
        prereq_str, coreq_str, either_str = self._segment_modalities(cleaned_text)

        # Compile each modality buffer
        prereq_tokens = self._normalize_tokens(prereq_str)
        coreq_tokens = self._normalize_tokens(coreq_str)
        either_tokens = self._normalize_tokens(either_str)

        prereq_ast = self._compile_shunting_yard(prereq_tokens)
        coreq_ast = self._compile_shunting_yard(coreq_tokens)
        either_ast = self._compile_shunting_yard(either_tokens)

        # Conjoin double-negative positive courses
        if pos_nodes:
            all_prereqs = ([prereq_ast] if prereq_ast else []) + pos_nodes
            prereq_ast = LogicNode(op="AND", children=all_prereqs)

        # Conjoin anti-requisites with prerequisites
        anti_req_ast = None
        if neg_nodes:
            anti_req_ast = neg_nodes[0] if len(neg_nodes) == 1 else LogicNode(op="AND", children=neg_nodes)
            if prereq_ast:
                prereq_ast = LogicNode(op="AND", children=neg_nodes + [prereq_ast])
            else:
                prereq_ast = anti_req_ast

        return {
            "raw": raw_text,
            "permission_allowed": permission_allowed,
            "prerequisites_formula": prereq_ast.to_formula() if prereq_ast else None,
            "corequisites_formula": coreq_ast.to_formula() if coreq_ast else None,
            "prereq_or_coreq_formula": either_ast.to_formula() if either_ast else None,
            "anti_requisites_formula": anti_req_ast.to_formula() if anti_req_ast else None,
            "prerequisites_ast": prereq_ast.to_dict() if prereq_ast else None,
            "corequisites_ast": coreq_ast.to_dict() if coreq_ast else None,
            "prereq_or_coreq_ast": either_ast.to_dict() if either_ast else None,
            "anti_requisites_ast": anti_req_ast.to_dict() if anti_req_ast else None,
        }

    @staticmethod
    def evaluate_ast(node: Optional[Dict[str, Any]], completed_set: Set[str]) -> bool:
        """Evaluates an AST dictionary against a set of completed normalized course IDs."""
        if not node:
            return True
        ntype = node.get("type")
        if ntype == "COURSE":
            return normalize_id(node.get("course", "")) in completed_set
        if ntype == "QUANTIFIER":
            subj = (node.get("subject") or "").upper()
            count = sum(1 for c in completed_set if c.startswith(subj))
            return count >= int(node.get("min_count", 1))
        if ntype == "NOT":
            return not GeneralizedPrerequisiteCompiler.evaluate_ast(node.get("operand"), completed_set)
        if ntype == "AND":
            return all(GeneralizedPrerequisiteCompiler.evaluate_ast(op, completed_set) for op in node.get("operands", []))
        if ntype == "OR":
            return any(GeneralizedPrerequisiteCompiler.evaluate_ast(op, completed_set) for op in node.get("operands", []))
        return True

    @staticmethod
    def get_missing_requirements(node: Optional[Dict[str, Any]], completed_set: Set[str]) -> Tuple[bool, List[str]]:
        """
        Recursively checks an AST against completed courses, returning:
        (is_met, list_of_unfulfilled_clauses).
        """
        if not node:
            return True, []

        ntype = node.get("type")
        if ntype == "COURSE":
            cid = node.get("course", "")
            if normalize_id(cid) in completed_set:
                return True, []
            return False, [format_id(cid)]

        if ntype == "QUANTIFIER":
            subj = (node.get("subject") or "").upper()
            needed = int(node.get("min_count", 1))
            count = sum(1 for c in completed_set if c.startswith(subj))
            if count >= needed:
                return True, []
            return False, [f"{needed - count} more {subj} course{'s' if (needed - count) > 1 else ''}"]

        if ntype == "NOT":
            operand = node.get("operand")
            if operand and operand.get("type") == "COURSE":
                forbidden = operand.get("course", "")
                if normalize_id(forbidden) in completed_set:
                    return False, [f"Excluded (already completed anti-requisite {format_id(forbidden)})"]
            return True, []

        if ntype == "OR":
            for op in node.get("operands", []):
                met, _ = GeneralizedPrerequisiteCompiler.get_missing_requirements(op, completed_set)
                if met:
                    return True, []
            or_parts = []
            for op in node.get("operands", []):
                _, miss = GeneralizedPrerequisiteCompiler.get_missing_requirements(op, completed_set)
                if miss:
                    or_parts.append(" and ".join(miss))
            return False, [" or ".join(or_parts)] if or_parts else []

        if ntype == "AND":
            missing_items = []
            all_met = True
            for op in node.get("operands", []):
                met, miss = GeneralizedPrerequisiteCompiler.get_missing_requirements(op, completed_set)
                if not met:
                    all_met = False
                    missing_items.extend(miss)
            return all_met, missing_items

        return True, []


# Global singleton instance
prerequisite_compiler = GeneralizedPrerequisiteCompiler()