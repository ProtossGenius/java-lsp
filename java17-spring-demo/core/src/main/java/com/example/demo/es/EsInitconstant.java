package com.example.demo.es;

import java.util.List;
import java.util.Map;
import java.util.function.Function;

import co.elastic.clients.elasticsearch._types.mapping.DynamicTemplate;
import co.elastic.clients.elasticsearch._types.mapping.TypeMapping;
import co.elastic.clients.util.ObjectBuilder;

public final class EsInitconstant {

  public static final Function<TypeMapping.Builder, ObjectBuilder<TypeMapping>> MEtA_OBJECT_MAPPING = builder -> builder
      .properties("name", property -> property.keyword(keyword -> keyword))
      .dynamicTemplates(List.of(
          Map.of(
              "fieldDetailMapLong",
              new DynamicTemplate.Builder()
                  .pathMatch("fieldDetailMap.*")
                  .matchMappingType("long")
                  .mapping(property -> property.scaledFloat(number -> number.scalingFactor(100.0)))
                  .build()
          ),
          Map.of(
              "fieldDetailMapDouble",
              new DynamicTemplate.Builder()
                  .pathMatch("fieldDetailMap.*")
                  .matchMappingType("double")
                  .mapping(property -> property.scaledFloat(number -> number.scalingFactor(100.0)))
                  .build()
          )
      ));

  private EsInitconstant() {
  }
}
