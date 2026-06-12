package com.example.demo.service;

import com.example.demo.api.Greeter;

public class DefaultGreeter implements Greeter {

    @Override
    public String greet(String name) {
        return String.format("hello %s", name);
    }
}

