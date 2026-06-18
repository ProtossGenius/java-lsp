package java

import (
	"strings"
	"unicode"

	"github.com/ProtossGenius/java-lsp/pkg/syntax"
)

type FileInfo struct {
	PackageName string
	Imports     map[string]string
	Types       []TypeInfo
}

type TypeInfo struct {
	Kind          string
	Name          string
	Header        string
	Documentation string
	Annotations   []string
	Fields        []FieldInfo
	Methods       []MethodInfo
	Line          int
}

type FieldInfo struct {
	Name        string
	Type        string
	Annotations []string
	Line        int
}

type MethodInfo struct {
	Name          string
	ReturnType    string
	Params        []ParamInfo
	Annotations   []string
	Documentation string
	Line          int
	IsStatic      bool
}

type ParamInfo struct {
	Name string
	Type string
}

type tokenKind int

const (
	tokenIdentifier tokenKind = iota
	tokenKeyword
	tokenSymbol
	tokenString
	tokenComment
	tokenNumber
)

type token struct {
	kind   tokenKind
	lexeme string
	line   int
	column int
	offset int
	end    int
}

var javaKeywords = map[string]struct{}{
	"package": {}, "import": {}, "class": {}, "interface": {}, "record": {}, "enum": {},
	"public": {}, "protected": {}, "private": {}, "abstract": {}, "final": {}, "static": {},
	"default": {}, "native": {}, "synchronized": {}, "extends": {}, "implements": {},
	"throws": {}, "void": {}, "new": {}, "return": {}, "if": {}, "for": {}, "while": {},
	"switch": {}, "catch": {}, "try": {}, "super": {}, "this": {}, "transient": {}, "volatile": {},
}

func Analyze(text string) FileInfo {
	tokens := lex(text)
	parser := analysisParser{
		text:   text,
		tokens: tokens,
		file: FileInfo{
			Imports: make(map[string]string),
			Types:   make([]TypeInfo, 0),
		},
	}
	parser.parse()
	return parser.file
}

type analysisParser struct {
	text   string
	tokens []token
	index  int
	file   FileInfo
}

func (p *analysisParser) parse() {
	var pendingDoc string
	pendingAnnotations := make([]string, 0)

	for p.index < len(p.tokens) {
		tok := p.tokens[p.index]
		switch {
		case tok.kind == tokenComment:
			if isDocComment(tok.lexeme) {
				pendingDoc = cleanDocComment(tok.lexeme)
			}
			p.index++
		case tok.lexeme == "@":
			annotation := p.parseAnnotation()
			if annotation != "" {
				pendingAnnotations = append(pendingAnnotations, annotation)
			}
		case tok.lexeme == "package":
			p.file.PackageName = p.parseQualifiedName()
			pendingDoc = ""
			pendingAnnotations = pendingAnnotations[:0]
		case tok.lexeme == "import":
			name := p.parseQualifiedName()
			if name != "" {
				p.file.Imports[lastQualifiedSegment(name)] = name
			}
			pendingDoc = ""
			pendingAnnotations = pendingAnnotations[:0]
		case isTypeKeyword(tok.lexeme):
			typeInfo := p.parseType(pendingDoc, cloneStrings(pendingAnnotations))
			if typeInfo.Name != "" {
				p.file.Types = append(p.file.Types, typeInfo)
			}
			pendingDoc = ""
			pendingAnnotations = pendingAnnotations[:0]
		default:
			if !isModifierToken(tok.lexeme) {
				pendingDoc = ""
				pendingAnnotations = pendingAnnotations[:0]
			}
			p.index++
		}
	}
}

func (p *analysisParser) parseQualifiedName() string {
	p.index++
	start := p.index
	for p.index < len(p.tokens) {
		tok := p.tokens[p.index]
		if tok.lexeme == ";" {
			break
		}
		p.index++
	}
	name := joinQualifiedTokens(p.tokens[start:p.index])
	if p.index < len(p.tokens) && p.tokens[p.index].lexeme == ";" {
		p.index++
	}
	return strings.TrimSpace(name)
}

func (p *analysisParser) parseAnnotation() string {
	p.index++ // skip @
	start := p.index
	if p.index < len(p.tokens) && isIdentifierToken(p.tokens[p.index]) {
		p.index++
		for p.index+1 < len(p.tokens) && p.tokens[p.index].lexeme == "." && isIdentifierToken(p.tokens[p.index+1]) {
			p.index += 2
		}
	}
	name := joinQualifiedTokens(p.tokens[start:p.index])
	if p.index < len(p.tokens) && p.tokens[p.index].lexeme == "(" {
		p.skipBalanced("(", ")")
	}
	return name
}

func (p *analysisParser) skipBalanced(open, close string) {
	depth := 0
	for p.index < len(p.tokens) {
		switch p.tokens[p.index].lexeme {
		case open:
			depth++
		case close:
			depth--
			if depth == 0 {
				p.index++
				return
			}
		}
		p.index++
	}
}

func (p *analysisParser) parseType(doc string, annotations []string) TypeInfo {
	typeTok := p.tokens[p.index]
	typeInfo := TypeInfo{
		Kind:          typeTok.lexeme,
		Documentation: doc,
		Annotations:   annotations,
		Line:          typeTok.line,
		Fields:        make([]FieldInfo, 0),
		Methods:       make([]MethodInfo, 0),
	}

	headerStart := p.index
	p.index++
	for p.index < len(p.tokens) {
		tok := p.tokens[p.index]
		if typeInfo.Name == "" && (tok.kind == tokenIdentifier || tok.kind == tokenKeyword) {
			typeInfo.Name = tok.lexeme
		}
		if tok.lexeme == "{" {
			typeInfo.Header = strings.TrimSpace(p.text[p.tokens[headerStart].offset:tok.offset])
			p.index++
			break
		}
		p.index++
	}
	p.parseTypeBody(&typeInfo)
	return typeInfo
}

func (p *analysisParser) parseTypeBody(typeInfo *TypeInfo) {
	bodyDepth := 1
	var pendingDoc string
	pendingAnnotations := make([]string, 0)
	memberTokens := make([]token, 0)

	for p.index < len(p.tokens) {
		tok := p.tokens[p.index]
		if tok.kind == tokenComment && bodyDepth == 1 && len(memberTokens) == 0 {
			if isDocComment(tok.lexeme) {
				pendingDoc = cleanDocComment(tok.lexeme)
			}
			p.index++
			continue
		}
		if bodyDepth == 1 && len(memberTokens) == 0 && tok.lexeme == "@" {
			annotation := p.parseAnnotation()
			if annotation != "" {
				pendingAnnotations = append(pendingAnnotations, annotation)
			}
			continue
		}

		switch tok.lexeme {
		case "{":
			if bodyDepth == 1 && len(memberTokens) > 0 {
				p.parseMemberTokens(memberTokens, pendingDoc, pendingAnnotations, typeInfo, true)
				memberTokens = memberTokens[:0]
				pendingDoc = ""
				pendingAnnotations = pendingAnnotations[:0]
			}
			bodyDepth++
			p.index++
		case "}":
			bodyDepth--
			p.index++
			if bodyDepth == 0 {
				return
			}
		case ";":
			if bodyDepth == 1 {
				p.parseMemberTokens(memberTokens, pendingDoc, pendingAnnotations, typeInfo, false)
				memberTokens = memberTokens[:0]
				pendingDoc = ""
				pendingAnnotations = pendingAnnotations[:0]
			}
			p.index++
		default:
			if bodyDepth == 1 {
				memberTokens = append(memberTokens, tok)
			}
			p.index++
		}
	}
}

func (p *analysisParser) parseMemberTokens(tokens []token, doc string, annotations []string, typeInfo *TypeInfo, hasBody bool) {
	if len(tokens) == 0 {
		return
	}
	if looksLikeNestedType(tokens) {
		return
	}
	openIndex := indexOf(tokens, "(")
	eqIndex := indexOf(tokens, "=")
	if openIndex >= 0 && (eqIndex < 0 || openIndex < eqIndex) {
		method := parseMethodInfo(tokens, doc, annotations)
		if method.Name != "" {
			typeInfo.Methods = append(typeInfo.Methods, method)
		}
		return
	}
	field := parseFieldInfo(tokens, annotations)
	if field.Name != "" {
		typeInfo.Fields = append(typeInfo.Fields, field)
	}
	_ = hasBody
}

func parseMethodInfo(tokens []token, doc string, annotations []string) MethodInfo {
	openIndex := indexOf(tokens, "(")
	if openIndex <= 0 {
		return MethodInfo{}
	}
	nameIndex := openIndex - 1
	for nameIndex >= 0 && !isIdentifierToken(tokens[nameIndex]) {
		nameIndex--
	}
	if nameIndex < 0 {
		return MethodInfo{}
	}

	signatureTokens := stripDecorators(tokens[:nameIndex+1])
	if len(signatureTokens) < 2 {
		return MethodInfo{}
	}

	nameTok := tokens[nameIndex]
	returnTypeTokens := signatureTokens[:len(signatureTokens)-1]
	method := MethodInfo{
		Name:          nameTok.lexeme,
		ReturnType:    syntax.NormalizeTypeName(joinTypeTokens(returnTypeTokens)),
		Params:        parseParamInfo(tokens[openIndex+1 : matchingParen(tokens, openIndex)]),
		Annotations:   annotations,
		Documentation: doc,
		Line:          nameTok.line,
		IsStatic:      containsLexeme(tokens, "static"),
	}
	return method
}

func parseFieldInfo(tokens []token, annotations []string) FieldInfo {
	end := len(tokens)
	if eq := indexOf(tokens, "="); eq >= 0 {
		end = eq
	}
	usable := stripDecorators(tokens[:end])
	if len(usable) < 2 {
		return FieldInfo{}
	}
	nameTok := usable[len(usable)-1]
	typeTokens := usable[:len(usable)-1]
	return FieldInfo{
		Name:        nameTok.lexeme,
		Type:        syntax.NormalizeTypeName(joinTypeTokens(typeTokens)),
		Annotations: annotations,
		Line:        nameTok.line,
	}
}

func parseParamInfo(tokens []token) []ParamInfo {
	params := make([]ParamInfo, 0)
	if len(tokens) == 0 {
		return params
	}
	start := 0
	angleDepth := 0
	for i, tok := range tokens {
		switch tok.lexeme {
		case "<":
			angleDepth++
		case ">":
			if angleDepth > 0 {
				angleDepth--
			}
		case ",":
			if angleDepth == 0 {
				if param := parseSingleParam(tokens[start:i]); param.Name != "" {
					params = append(params, param)
				}
				start = i + 1
			}
		}
	}
	if param := parseSingleParam(tokens[start:]); param.Name != "" {
		params = append(params, param)
	}
	return params
}

func parseSingleParam(tokens []token) ParamInfo {
	usable := stripDecorators(tokens)
	if len(usable) < 2 {
		return ParamInfo{}
	}
	nameTok := usable[len(usable)-1]
	typeTokens := usable[:len(usable)-1]
	return ParamInfo{
		Name: nameTok.lexeme,
		Type: syntax.NormalizeTypeName(joinTypeTokens(typeTokens)),
	}
}

func stripDecorators(tokens []token) []token {
	filtered := make([]token, 0, len(tokens))
	angleDepth := 0
	for i := 0; i < len(tokens); i++ {
		tok := tokens[i]
		switch {
		case tok.lexeme == "@":
			for i+1 < len(tokens) {
				i++
				if tokens[i].lexeme == "(" {
					depth := 1
					for i+1 < len(tokens) && depth > 0 {
						i++
						if tokens[i].lexeme == "(" {
							depth++
						} else if tokens[i].lexeme == ")" {
							depth--
						}
					}
					break
				}
				if tokens[i].lexeme != "." && !isIdentifierToken(tokens[i]) {
					i--
					break
				}
			}
		case tok.lexeme == "<":
			angleDepth++
			filtered = append(filtered, tok)
		case tok.lexeme == ">":
			if angleDepth > 0 {
				angleDepth--
			}
			filtered = append(filtered, tok)
		case angleDepth == 0 && isModifierToken(tok.lexeme):
			continue
		default:
			filtered = append(filtered, tok)
		}
	}
	return filtered
}

func looksLikeNestedType(tokens []token) bool {
	for _, tok := range tokens {
		if isTypeKeyword(tok.lexeme) {
			return true
		}
	}
	return false
}

func matchingParen(tokens []token, openIndex int) int {
	depth := 0
	for i := openIndex; i < len(tokens); i++ {
		switch tokens[i].lexeme {
		case "(":
			depth++
		case ")":
			depth--
			if depth == 0 {
				return i
			}
		}
	}
	return len(tokens)
}

func indexOf(tokens []token, lexeme string) int {
	for i, tok := range tokens {
		if tok.lexeme == lexeme {
			return i
		}
	}
	return -1
}

func containsLexeme(tokens []token, lexeme string) bool {
	return indexOf(tokens, lexeme) >= 0
}

func joinQualifiedTokens(tokens []token) string {
	var b strings.Builder
	for _, tok := range tokens {
		if tok.lexeme == "." {
			b.WriteString(".")
			continue
		}
		b.WriteString(tok.lexeme)
	}
	return b.String()
}

func joinTypeTokens(tokens []token) string {
	var b strings.Builder
	for i, tok := range tokens {
		if i > 0 && needsSpace(tokens[i-1], tok) {
			b.WriteByte(' ')
		}
		b.WriteString(tok.lexeme)
	}
	return strings.TrimSpace(b.String())
}

func needsSpace(prev, current token) bool {
	noSpaceAfter := map[string]struct{}{"<": {}, "(": {}, "[": {}, ".": {}, "@": {}, ",": {}}
	noSpaceBefore := map[string]struct{}{"<": {}, ">": {}, ")": {}, "]": {}, ".": {}, ",": {}, ";": {}}
	if _, ok := noSpaceAfter[prev.lexeme]; ok {
		return false
	}
	if _, ok := noSpaceBefore[current.lexeme]; ok {
		return false
	}
	return true
}

func isTypeKeyword(lexeme string) bool {
	switch lexeme {
	case "class", "interface", "record", "enum":
		return true
	default:
		return false
	}
}

func isModifierToken(lexeme string) bool {
	switch lexeme {
	case "public", "protected", "private", "abstract", "final", "static", "sealed", "non-sealed", "default", "native", "synchronized", "transient", "volatile":
		return true
	default:
		return false
	}
}

func isIdentifierToken(tok token) bool {
	return tok.kind == tokenIdentifier || tok.kind == tokenKeyword
}

func isDocComment(text string) bool {
	return strings.HasPrefix(strings.TrimSpace(text), "/**")
}

func cleanDocComment(text string) string {
	lines := strings.Split(text, "\n")
	parts := make([]string, 0, len(lines))
	for _, line := range lines {
		trimmed := strings.TrimSpace(line)
		trimmed = strings.TrimPrefix(trimmed, "/**")
		trimmed = strings.TrimPrefix(trimmed, "/*")
		trimmed = strings.TrimSuffix(trimmed, "*/")
		trimmed = strings.TrimSpace(strings.TrimPrefix(trimmed, "*"))
		if trimmed != "" {
			parts = append(parts, trimmed)
		}
	}
	return strings.Join(parts, "\n")
}

func lastQualifiedSegment(value string) string {
	if index := strings.LastIndex(value, "."); index >= 0 {
		return value[index+1:]
	}
	return value
}

func lex(text string) []token {
	tokens := make([]token, 0)
	line := 0
	column := 0
	for i := 0; i < len(text); {
		ch := rune(text[i])
		if ch == '\n' {
			line++
			column = 0
			i++
			continue
		}
		if unicode.IsSpace(ch) {
			column++
			i++
			continue
		}
		start, startLine, startCol := i, line, column

		if ch == '/' && i+1 < len(text) && text[i+1] == '/' {
			i += 2
			column += 2
			for i < len(text) && text[i] != '\n' {
				i++
				column++
			}
			tokens = append(tokens, token{kind: tokenComment, lexeme: text[start:i], line: startLine, column: startCol, offset: start, end: i})
			continue
		}
		if ch == '/' && i+1 < len(text) && text[i+1] == '*' {
			i += 2
			column += 2
			for i < len(text)-1 {
				if text[i] == '\n' {
					line++
					column = 0
					i++
					continue
				}
				if text[i] == '*' && text[i+1] == '/' {
					i += 2
					column += 2
					break
				}
				i++
				column++
			}
			tokens = append(tokens, token{kind: tokenComment, lexeme: text[start:i], line: startLine, column: startCol, offset: start, end: i})
			continue
		}
		if ch == '"' || ch == '\'' {
			quote := byte(ch)
			i++
			column++
			for i < len(text) {
				if text[i] == '\\' {
					i += 2
					column += 2
					continue
				}
				if text[i] == quote {
					i++
					column++
					break
				}
				if text[i] == '\n' {
					line++
					column = 0
					i++
					continue
				}
				i++
				column++
			}
			tokens = append(tokens, token{kind: tokenString, lexeme: text[start:i], line: startLine, column: startCol, offset: start, end: i})
			continue
		}
		if isIdentifierStart(ch) {
			i++
			column++
			for i < len(text) && isIdentifierPart(rune(text[i])) {
				i++
				column++
			}
			lexeme := text[start:i]
			kind := tokenIdentifier
			if _, ok := javaKeywords[lexeme]; ok {
				kind = tokenKeyword
			}
			tokens = append(tokens, token{kind: kind, lexeme: lexeme, line: startLine, column: startCol, offset: start, end: i})
			continue
		}
		if unicode.IsDigit(ch) {
			i++
			column++
			for i < len(text) && (unicode.IsDigit(rune(text[i])) || text[i] == '.') {
				i++
				column++
			}
			tokens = append(tokens, token{kind: tokenNumber, lexeme: text[start:i], line: startLine, column: startCol, offset: start, end: i})
			continue
		}

		lexeme := string(ch)
		if ch == '.' && i+2 < len(text) && text[i+1] == '.' && text[i+2] == '.' {
			lexeme = "..."
			i += 3
			column += 3
		} else {
			i++
			column++
		}
		tokens = append(tokens, token{kind: tokenSymbol, lexeme: lexeme, line: startLine, column: startCol, offset: start, end: i})
	}
	return tokens
}

func isIdentifierStart(r rune) bool {
	return r == '_' || r == '$' || unicode.IsLetter(r)
}

func isIdentifierPart(r rune) bool {
	return isIdentifierStart(r) || unicode.IsDigit(r)
}
