package lsp

import (
	"errors"
	"strings"
)

func signatureHelpAtPosition(text string, pos Position) (*SignatureHelp, error) {
	offset, err := offsetForPosition(text, pos)
	if err != nil {
		return nil, err
	}

	method, activeParameter, ok := invocationAtOffset(text, offset)
	if !ok {
		return nil, nil
	}

	signature, ok := lookupSignature(method)
	if !ok {
		return &SignatureHelp{
			Signatures:      []SignatureInformation{},
			ActiveSignature: 0,
			ActiveParameter: activeParameter,
		}, nil
	}
	if activeParameter >= len(signature.Parameters) && len(signature.Parameters) > 0 {
		activeParameter = len(signature.Parameters) - 1
	}

	return &SignatureHelp{
		Signatures:      []SignatureInformation{signature},
		ActiveSignature: 0,
		ActiveParameter: activeParameter,
	}, nil
}

func offsetForPosition(text string, pos Position) (int, error) {
	lines := strings.Split(text, "\n")
	if pos.Line < 0 || pos.Line >= len(lines) {
		return 0, errors.New("position line is out of range")
	}

	offset := 0
	for i := 0; i < pos.Line; i++ {
		offset += len([]rune(lines[i])) + 1
	}
	lineRunes := []rune(lines[pos.Line])
	character := pos.Character
	if character < 0 {
		return 0, errors.New("position character is out of range")
	}
	if character > len(lineRunes) {
		character = len(lineRunes)
	}
	return offset + character, nil
}

func invocationAtOffset(text string, offset int) (string, int, bool) {
	runes := []rune(text)
	if offset < 0 || offset > len(runes) {
		return "", 0, false
	}

	openIndex := -1
	depth := 0
	for i := offset - 1; i >= 0; i-- {
		switch runes[i] {
		case ')':
			depth++
		case '(':
			if depth == 0 {
				openIndex = i
				i = -1
				break
			}
			depth--
		}
	}
	if openIndex < 0 {
		return "", 0, false
	}

	end := openIndex
	for end > 0 && unicodeWhitespace(runes[end-1]) {
		end--
	}
	start := end
	for start > 0 && isMethodTokenPart(runes[start-1]) {
		start--
	}
	if start == end {
		return "", 0, false
	}

	activeParameter := 0
	nestedDepth := 0
	for i := openIndex + 1; i < offset && i < len(runes); i++ {
		switch runes[i] {
		case '(':
			nestedDepth++
		case ')':
			if nestedDepth > 0 {
				nestedDepth--
			}
		case ',':
			if nestedDepth == 0 {
				activeParameter++
			}
		}
	}

	return string(runes[start:end]), activeParameter, true
}

func lookupSignature(method string) (SignatureInformation, bool) {
	switch {
	case method == "String.format" || strings.HasSuffix(method, ".format"):
		return SignatureInformation{
			Label: "String format(String format, Object... args)",
			Parameters: []ParameterInformation{
				{Label: "String format"},
				{Label: "Object... args"},
			},
		}, true
	default:
		return SignatureInformation{}, false
	}
}

func unicodeWhitespace(r rune) bool {
	return r == ' ' || r == '\t' || r == '\n' || r == '\r'
}

func isMethodTokenPart(r rune) bool {
	return isJavaIdentifierPart(r) || r == '.'
}
