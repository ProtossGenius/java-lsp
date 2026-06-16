package com.example.demo.api;

/**
 * Produces a greeting for a caller.
 */
public interface Greeter {

    /**
     * Returns a greeting message for the provided name.
     */
    String greet(String name);
}
