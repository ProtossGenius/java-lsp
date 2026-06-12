package config

import (
	"net/http"
	"net/url"
)

type Config struct {
	ProxyURL    string
	StoragePath string
}

func (c Config) HTTPClient() (*http.Client, error) {
	transport := http.DefaultTransport.(*http.Transport).Clone()
	if c.ProxyURL != "" {
		proxyURL, err := url.Parse(c.ProxyURL)
		if err != nil {
			return nil, err
		}
		transport.Proxy = http.ProxyURL(proxyURL)
	}

	return &http.Client{Transport: transport}, nil
}
