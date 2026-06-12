package com.example.gradledemo.order;

import lombok.Getter;

public class OrderSummary {

    @Getter
    private String orderNumber;

    private long totalCents;

    public long totalCents() {
        return totalCents;
    }
}

